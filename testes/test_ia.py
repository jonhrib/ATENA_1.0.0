"""Integração com a API da OpenAI, testada contra um servidor local que imita a API (nada sai da máquina)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from atena import config, segredos
from atena.servicos import codificacao as cod, documentos, ia, protegido, temas as tm, transcricoes as tr
from atena.servicos.projetos import ErroValidacao
from conftest import anonimizar_tudo


class Falso(BaseHTTPRequestHandler):
    resposta = {}
    recebidos = []
    rejeitar_json = False

    def log_message(self, *a):
        pass

    def _json(self, codigo, obj):
        b = json.dumps(obj).encode()
        self.send_response(codigo); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer sk-teste-123":
            return self._json(401, {"error": {"message": "invalid key"}})
        self._json(200, {"data": [{"id": "gpt-6-luna"}, {"id": "gpt-6.1-terra"}]})

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Falso.recebidos.append(corpo)
        if Falso.rejeitar_json and "response_format" in corpo:
            return self._json(400, {"error": {"message": "Unsupported parameter: 'response_format'"}})
        conteudo = json.dumps(Falso.resposta)
        if Falso.rejeitar_json:
            conteudo = "```json\n" + conteudo + "\n```"
        self._json(200, {"choices": [{"message": {"content": conteudo}}],
                         "usage": {"prompt_tokens": 1200, "completion_tokens": 300}})


@pytest.fixture
def api_falsa(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    config.salvar({"ia_habilitada": True, "ia_url_base": f"http://127.0.0.1:{srv.server_port}/v1", "ia_limite_mensal_usd": 5.0})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste-123")
    Falso.recebidos.clear()
    Falso.rejeitar_json = False
    yield Falso
    srv.shutdown()


@pytest.fixture
def corpus(projeto_demo):
    p = projeto_demo
    for d in documentos.listar(p):
        anonimizar_tudo(p, d["id"])
    return p


def test_sem_chave_o_sistema_funciona_e_o_envio_e_bloqueado(corpus):
    config.salvar({"ia_habilitada": True})
    d = documentos.listar(corpus)[0]
    i = ia.preparar(corpus, "sugerir_codigos", {"passagens": [{"documento_id": d["id"], "inicio": 0, "fim": 300}]})
    assert i["situacao"] == "preparada"
    with pytest.raises(ErroValidacao, match="chave"):
        ia.enviar(corpus, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    assert ia.testar_conexao()["ok"] is False


def test_desativada_e_sem_confirmacao(corpus, api_falsa):
    d = documentos.listar(corpus)[0]
    i = ia.preparar(corpus, "sugerir_codigos", {"passagens": [{"documento_id": d["id"], "inicio": 0, "fim": 300}]})
    with pytest.raises(ErroValidacao):
        ia.enviar(corpus, i["id"], confirmo_anonimizado=False, confirmo_envio=True)
    config.salvar({"ia_habilitada": False})
    with pytest.raises(ErroValidacao, match="desativada"):
        ia.enviar(corpus, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    assert api_falsa.recebidos == []


def test_teste_de_conexao(api_falsa):
    r = ia.testar_conexao()
    assert r["ok"] and r["modelo_disponivel"]


def test_sugestao_de_codigos_validada_e_aplicada_so_com_aceite(corpus, api_falsa):
    p = corpus
    d = documentos.listar(p)[0]
    t = tr.versao_atual(p, d["id"])["texto"]
    ini = t.index("Puxada.")
    fim = t.index("trancar.") + len("trancar.")
    api_falsa.resposta = {"sugestoes": [
        {"nome": "Sobrecarga de trabalho e estudo", "descricao": "d", "justificativa": "j", "codigo_existente_id": None,
         "evidencias": [{"passagem": "P1", "citacao": "eu trabalhava à noite numa loja"},
                        {"passagem": "P1", "citacao": "FRASE INVENTADA QUE NÃO EXISTE"},
                        {"passagem": "P9", "citacao": "x"}]}]}
    i = ia.preparar(p, "sugerir_codigos", {"passagens": [{"documento_id": d["id"], "inicio": ini, "fim": fim}]})
    enviado = i["corpo"]["messages"][1]["content"]
    assert "PARTICIPANTE_001" in enviado and "Marina" not in enviado
    i = ia.enviar(p, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    assert i["situacao"] == "respondida" and i["tokens_entrada"] == 1200 and i["custo_estimado"] > 0
    s = i["sugestoes"][0]
    assert len(s["conteudo_obj"]["evidencias"]) == 1 and len(s["alertas_obj"]) == 2   # citação inventada descartada
    assert cod.listar_codigos(p) == []                                                 # nada aplicado sem decisão
    ia.decidir_sugestao(p, s["id"], "editada", editado={"nome": "Conciliar trabalho e estudo"}, observacao="ajuste de nome")
    c = cod.listar_codigos(p)[0]
    assert c["nome"] == "Conciliar trabalho e estudo" and c["origem"] == "ia" and c["n_fragmentos"] == 1
    ev = cod.evidencias(p, c["id"])[0]
    assert ev["texto"] == "eu trabalhava à noite numa loja" and ev["participante_codigo"] == "PARTICIPANTE_001"
    origens = {r[0] for r in p.con.execute("SELECT origem FROM historico WHERE entidade IN ('ia','sugestao','codigo')")}
    assert {"pesquisador", "ia"} <= origens
    with pytest.raises(ErroValidacao):
        ia.decidir_sugestao(p, s["id"], "aceita")


def test_envio_bloqueado_quando_ha_valor_do_mapeamento(corpus, api_falsa):
    p = corpus
    d = documentos.criar_manual(p, "PARTICIPANTE_009 - 10:00:\nConversei com Marina Quaresma ontem.", ja_anonimizado=True)
    i = ia.preparar(p, "sugerir_codigos", {"passagens": [{"documento_id": d["id"], "inicio": 0, "fim": 200}]})
    assert any(b["fonte"] == "mapeamento" for b in i["verificacao"]["bloqueantes"])
    with pytest.raises(ErroValidacao, match="mapeamento"):
        ia.enviar(p, i["id"], confirmo_anonimizado=True, confirmo_envio=True, revisei_alertas=True)
    assert api_falsa.recebidos == []


def test_mapeamento_nunca_faz_parte_do_envio(corpus, api_falsa):
    p = corpus
    api_falsa.resposta = {"agrupamentos": [{"tema": "T", "descricao": "d", "codigos": [1, 2, 999], "justificativa": "j"}], "relacoes": []}
    d = documentos.listar(p)[0]
    t = tr.versao_atual(p, d["id"])["texto"]
    for trecho, nome in [("quase desisti", "Desistência"), ("Eu senti que fazia parte", "Pertencimento")]:
        k = t.index(trecho)
        cod.codificar_selecao(p, d["id"], k, k + len(trecho), novo_codigo={"nome": nome})
    i = ia.preparar(p, "sugerir_agrupamentos", {})
    ia.enviar(p, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    corpo = json.dumps(api_falsa.recebidos[-1], ensure_ascii=False)
    for ident in protegido.identidades(p):
        for v in [ident["valor"]] + ident["variantes"]:
            assert v not in corpo, v
    s = ia.listar_sugestoes(p, situacao="pendente")[0]
    assert s["conteudo_obj"]["codigos"] == [1, 2] and s["alertas_obj"]
    ia.decidir_sugestao(p, s["id"], "aceita")
    t2 = tm.listar(p)[0]
    assert t2["origem"] == "ia" and t2["situacao"] == "candidato" and t2["n_codigos"] == 2


def test_sintese_referencias_validadas(corpus, api_falsa):
    p = corpus
    d = documentos.listar(p)[0]
    t = tr.versao_atual(p, d["id"])["texto"]
    k = t.index("faca de dois gumes")
    f = cod.codificar_selecao(p, d["id"], k, k + 18, novo_codigo={"nome": "Ambivalência"})
    tema = tm.criar(p, "EJ", codigo_ids=[f["codigos"][0]["id"]])
    api_falsa.resposta = {"sintese": f"A EJ aparece como ambígua [F{f['id']}] e também [F777]. “Uma fala inventada pela IA que não existe nos dados”.",
                          "fragmentos_citados": [f"F{f['id']}"]}
    i = ia.preparar(p, "sintetizar_tema", {"tema_id": tema["id"]})
    i = ia.enviar(p, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    s = i["sugestoes"][0]
    assert f"[[frag:{f['id']}]]" in s["conteudo_obj"]["texto"] and "F777" not in s["conteudo_obj"]["texto"]
    assert len(s["alertas_obj"]) == 2
    from atena.servicos import relatorios as rel
    ia.decidir_sugestao(p, s["id"], "aceita")
    r = rel.listar(p)[0]
    assert r["tipo"] == "sinteses" and rel.MARCA_IA in rel.obter(p, r["id"])["versao"]["conteudo"]


def test_limite_mensal(corpus, api_falsa):
    config.salvar({"ia_limite_mensal_usd": 0.0000001})
    d = documentos.listar(corpus)[0]
    i = ia.preparar(corpus, "verificar_privacidade", {"documento_id": d["id"]})
    with pytest.raises(ErroValidacao, match="limite"):
        ia.enviar(corpus, i["id"], confirmo_anonimizado=True, confirmo_envio=True)


def test_chave_guardada_fora_do_banco(projeto, monkeypatch):
    segredos.salvar_chave("sk-segredo-local-999")
    assert segredos.obter_chave()[0] == "sk-segredo-local-999"
    for arq in projeto.pasta.glob("*.sqlite*"):
        assert b"sk-segredo" not in arq.read_bytes()
    assert b"sk-segredo-local-999" not in (config.pasta_dados() / "segredos.bin").read_bytes()
    segredos.remover_chave()
    assert segredos.obter_chave() == (None, None)


def test_modelo_sem_modo_json(corpus, api_falsa):
    api_falsa.rejeitar_json = True
    api_falsa.resposta = {"possiveis_identificadores": []}
    d = documentos.listar(corpus)[0]
    i = ia.preparar(corpus, "verificar_privacidade", {"documento_id": d["id"]})
    i = ia.enviar(corpus, i["id"], confirmo_anonimizado=True, confirmo_envio=True)
    assert i["situacao"] == "respondida" and len(api_falsa.recebidos) == 2
    assert api_falsa.recebidos[0]["messages"] == api_falsa.recebidos[1]["messages"]   # mesmo conteúdo autorizado
