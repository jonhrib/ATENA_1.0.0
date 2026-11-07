"""Critérios de aceitação 1–20, executados de ponta a ponta pela interface HTTP do ATENA,
sem internet (conexões externas bloqueadas) e sem chave de API."""
import io
import re
import socket

import pytest

from atena.web.app import criar_app
from servicos_auxiliares import Cliente

ENTREVISTA = """Entrevistadora Teste - 09:00:
Pode contar como foi o primeiro ano?

Joana Ficticia Moreira - 09:01:
Eu sou a Joana, estudo na Universidade do Vale Fictício. No primeiro ano eu quase desisti porque trabalhava o dia todo.
Meu e-mail é joana.ficticia@exemplo.com e meu celular (11) 91234-5678. A Fictícia Jr. me ajudou a ficar.

Entrevistadora Teste - 09:03:
E o que mais?

Joana Ficticia Moreira - 09:04:
A professora Lúcia Fagundes me orientou. Ainda assim a cobrança da empresa júnior às vezes me sobrecarregava.
"""


@pytest.fixture
def sem_internet(monkeypatch):
    original = socket.socket.connect

    def conectar(self, endereco):
        host = endereco[0] if isinstance(endereco, tuple) else endereco
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise OSError("Teste offline: conexão externa bloqueada")
        return original(self, endereco)
    monkeypatch.setattr(socket.socket, "connect", conectar)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))


def test_fluxo_completo_de_ponta_a_ponta(sem_internet):
    from atena import segredos
    assert segredos.obter_chave() == (None, None)          # 20. sem API configurada
    c = Cliente(criar_app("tok", testes=True))

    # 1. criar projeto
    r = c.post("/projetos/novo", nome="Pesquisa fictícia", pergunta="Como estudantes descrevem a permanência?",
               senha="senha-forte-1", senha2="senha-forte-1", termos_extra="Universidade do Vale Fictício\nFictícia Jr.")
    uid = re.search(r"/p/([0-9a-f]{32})/", r.location)[1]
    c.uid = uid
    assert "Código de recuperação" in c.get(f"/p/{uid}/recuperacao").text
    c.post(f"/p/{uid}/recuperacao/confirmar", confirmo="1")
    pid = c.post(f"/p/{uid}/participantes", meta_chave="Curso", meta_valor="Computação", situacao="realizada",
                 nome_real="Joana Ficticia Moreira", variantes="Joana")
    c.post(f"/p/{uid}/cofre/identidade", categoria="ENTREVISTADOR", valor="Entrevistadora Teste", variantes="")

    # 2. importar entrevista
    c.post(f"/p/{uid}/documentos/importar", arquivos=(io.BytesIO(ENTREVISTA.encode()), "joana_ficticia.txt"),
           tipo="entrevista", participante_id="1", titulo="Entrevista 1")
    assert "DOC_001" in c.get(f"/p/{uid}/documentos").text

    # 3. identificar possíveis informações pessoais
    c.post(f"/p/{uid}/anonimizacao/1")
    pag = c.get(f"/p/{uid}/anonimizacao/1").text
    for esperado in ("Joana Ficticia Moreira", "joana.ficticia@exemplo.com", "(11) 91234-5678", "Lúcia Fagundes"):
        assert esperado in pag
    # 4. revisar e aprovar
    c.post(f"/p/{uid}/anonimizacao/sessao/1/lote")
    pendentes = [int(x) for x in re.findall(r'data-itens="([\d,]+)"[^>]*', c.get(f"/p/{uid}/anonimizacao/1").text)
                 for x in x.split(",")]
    from atena.servicos import anonimizacao as an, projetos
    p = projetos.Projeto(uid)
    for it in an.itens(p, 1):
        if it["decisao"] == "pendente":
            c.api(f"/api/p/{uid}/anon/decidir", itens=[it["id"]], decisao="anonimizar" if it["categoria"] == "PESSOA" else "rejeitar",
                  categoria=it["categoria"], novo_codigo=True)
    p.fechar()
    # 5. gerar versão anonimizada
    r = c.post(f"/p/{uid}/anonimizacao/sessao/1/aplicar", confirmo="1", justificativa="conferido")
    texto = c.get(f"/p/{uid}/editor/1?modo=editar").text
    for nome in ("Joana", "joana.ficticia", "91234-5678", "Lúcia", "Entrevistadora Teste"):
        assert nome not in texto
    assert "PARTICIPANTE_001" in texto and "Fictícia Jr." in texto

    # 6. original preservado e mapeamento protegido
    r = c.get(f"/p/{uid}/documentos/1/original")
    assert r.data == ENTREVISTA.encode()
    r = c.post(f"/p/{uid}/cofre/mapeamento", _espera_erro=True, motivo="conferência", senha="errada-errada")
    assert "Joana Ficticia Moreira" not in r.text
    assert "Joana Ficticia Moreira" in c.post(f"/p/{uid}/cofre/mapeamento", motivo="conferência", senha="senha-forte-1").text

    # 7. editar a transcrição
    v = re.search(r'<textarea name="texto"[^>]*>(.*?)</textarea>', texto, re.S)[1]
    import html
    v = html.unescape(v)
    c.post(f"/p/{uid}/editor/1/salvar", texto=v.replace("E o que mais?", "E o que mais você lembra?"),
           observacao="ajuste de transcrição", versao_base="1")

    # 8–9. selecionar fragmento e criar código
    from atena.servicos import transcricoes
    p = projetos.Projeto(uid)
    atual = transcricoes.versao_atual(p, 1)["texto"]
    p.fechar()
    i = atual.index("quase desisti")
    j = c.api(f"/api/p/{uid}/fragmentos", documento_id=1, inicio=i, fim=i + len("quase desisti porque trabalhava o dia todo"),
              texto="quase desisti porque trabalhava o dia todo", novo_codigo={"nome": "Trabalho e vontade de desistir"},
              justificativa="relato direto")
    f1 = j["fragmento"]["id"]
    i = atual.index("me ajudou a ficar")
    f2 = c.api(f"/api/p/{uid}/fragmentos", documento_id=1, inicio=i, fim=i + 17, texto="me ajudou a ficar",
               novo_codigo={"nome": "EJ e permanência"})["fragmento"]["id"]
    i = atual.index("me sobrecarregava")
    c.api(f"/api/p/{uid}/fragmentos", documento_id=1, inicio=i, fim=i + 17, texto="me sobrecarregava", codigos=[2],
          novo_codigo={"nome": "Sobrecarga"})

    # 10. consultar todos os fragmentos do código
    pag = c.get(f"/p/{uid}/codigos/2").text
    assert "me ajudou a ficar" in pag and "me sobrecarregava" in pag and "PARTICIPANTE_001" in pag

    # 11. agrupar códigos em possíveis temas
    c.post(f"/p/{uid}/temas", nome="A EJ entre apoio e cobrança", descricao="", codigos=["2", "3"], justificativa="mesmo espaço")
    assert c.api_get(f"/api/p/{uid}/mapa")["elements"]

    # 12. revisar
    for n in ("1", "2"):
        c.post(f"/p/{uid}/temas/1", acao="revisar", nivel=n, coerencia="coerente", decisao="manter", observacao="ok")
    # 13–14. definir e nomear, com justificativa
    c.post(f"/p/{uid}/temas/1", acao="evidencia", fragmento_id=str(f2), marcar="1")
    c.post(f"/p/{uid}/temas/1/definir", nome="A empresa júnior como apoio e cobrança", conceito_central="ambivalência",
           descricao="desc", escopo="e", limites="l", relacao_pergunta="r", justificativa="sustentado pelos relatos", confirmar="1")
    assert "Definido" in c.get(f"/p/{uid}/definicao").text

    # 15. relatório com evidências
    r = c.post(f"/p/{uid}/relatorios", tipo="final")
    pag = c.get(r.location).text
    assert "me ajudou a ficar" in pag and f"fragmento nº {f2}" in pag

    # 16. histórico de todas as etapas
    hist = c.get(f"/p/{uid}/historico").text
    for op in ("importar", "aplicar", "nova versao", "associar", "criar", "revisar tema", "confirmar definicao", "consultar mapeamento"):
        assert op in hist, op
    assert "cadeia íntegra" in hist

    # 17. exportar
    r = c.post(f"/p/{uid}/exportar", conjunto="tudo", formato="xlsx")
    assert r.data[:2] == b"PK"
    r = c.post(r_loc(pag, uid), acao="exportar", formato="docx")
    assert r.data[:2] == b"PK"

    # 18. fechar e abrir novamente sem perder dados
    c2 = Cliente(criar_app("outro", testes=True), token="outro")
    assert "A empresa júnior como apoio e cobrança" in c2.get(f"/p/{uid}/definicao").text

    # 19. backup e restauração
    c2.post(f"/p/{uid}/backup", observacao="teste")
    from atena.servicos import backup
    arq = backup.listar(uid)[0]["arquivo"]
    r = c2.post("/backups/restaurar", arquivo=arq, modo="copia", justificativa="teste")
    uid2 = re.search(r"/p/([0-9a-f]{32})/", r.location)[1]
    assert uid2 != uid and "A empresa júnior como apoio e cobrança" in c2.get(f"/p/{uid2}/definicao").text


def r_loc(pagina_relatorio, uid):
    rid = re.search(r"/p/%s/relatorios/(\d+)" % uid, pagina_relatorio)[1]
    return f"/p/{uid}/relatorios/{rid}"
