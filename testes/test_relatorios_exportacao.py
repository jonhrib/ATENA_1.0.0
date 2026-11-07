import io
import json
import zipfile

import pytest

from atena.servicos import codificacao as cod, documentos, exportacao, protegido, relatorios as rel, temas as tm, transcricoes as tr
from atena.servicos.projetos import ErroValidacao
from conftest import SENHA, anonimizar_tudo

NOMES_REAIS = ["Marina", "Quaresma", "Caio", "Andrade", "Bianca", "Tavares", "Otávio", "Prates", "Helena Barros", "Rosa Lemos",
               "marina.quaresma", "98888-1234", "123.456.789-00", "2020123456", "Pesquisadora Demo"]


@pytest.fixture
def analise(projeto_demo):
    p = projeto_demo
    for d in documentos.listar(p):
        anonimizar_tudo(p, d["id"])
    d1 = documentos.listar(p)[0]
    t = tr.versao_atual(p, d1["id"])["texto"]
    i = t.index("faca de dois gumes")
    f = cod.codificar_selecao(p, d1["id"], i, i + 18, novo_codigo={"nome": "Ambivalência da EJ", "descricao": "d"})
    tema = tm.criar(p, "EJ ambígua", "descrição", codigo_ids=[f["codigos"][0]["id"]])
    tm.marcar_evidencia(p, tema["id"], f["id"])
    tm.definir(p, tema["id"], {"conceito_central": "cc", "descricao": "d"}, confirmar=True)
    return p, f, tema


def test_rascunhos_com_citacoes_vinculadas(analise):
    p, f, tema = analise
    rid = rel.gerar_rascunho(p, "final")
    c = rel.obter(p, rid)["versao"]["conteudo"]
    assert f"[[frag:{f['id']}]]" in c and "EJ ambígua" in c
    html = rel.html_relatorio(p, c)
    assert "faca de dois gumes" in html and "PARTICIPANTE_001" in html and f"fragmento nº {f['id']}" in html
    m = rel.obter(p, rel.gerar_rascunho(p, "metodologico"))["versao"]["conteudo"]
    assert "Braun e Clarke" in m and "Fase 1" in m


def test_verificacao_aponta_problemas(analise):
    p, f, _ = analise
    texto = ("# R\n\n[[frag:99999]]\n\nComo disse o participante, “eu sempre quis ser astronauta desde pequeno e nunca desisti”.\n\n"
             f"{rel.MARCA_IA}\n\nA Marina Quaresma disse algo.")
    probs = rel.verificar(p, texto)
    textos = " ".join(x["texto"] for x in probs)
    assert "inexistente" in textos and "não encontrado nas transcrições" in textos and "preliminares de IA" in textos
    assert "Marina" in textos  # o cofre está aberto: o nome real é detectado
    rid = rel.criar(p, "livre", "T", texto)
    with pytest.raises(ErroValidacao):
        rel.aprovar(p, rid, "ok")


def test_versoes_aprovacao_restauracao(analise):
    p, f, _ = analise
    rid = rel.criar(p, "livre", "T", "# Texto\n\nPrimeira versão.")
    rel.salvar_versao(p, rid, "# Texto\n\nSegunda versão.\n\n[[frag:%d]]" % f["id"], "acréscimo")
    rel.aprovar(p, rid, "revisado")
    assert rel.obter(p, rid)["situacao"] == "aprovado"
    rel.restaurar_versao(p, rid, 1, "voltar")
    r = rel.obter(p, rid)
    assert r["situacao"] == "rascunho" and r["versao"]["numero"] == 3 and "Primeira" in r["versao"]["conteudo"]


def _todo_texto(caminho):
    dados = caminho.read_bytes()
    if caminho.suffix in (".xlsx", ".docx", ".zip"):
        partes = []
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for n in z.namelist():
                b = z.read(n)
                if n.endswith((".xlsx", ".docx")):
                    with zipfile.ZipFile(io.BytesIO(b)) as z2:
                        partes += [z2.read(m).decode("utf-8", "ignore") for m in z2.namelist()]
                else:
                    partes.append(b.decode("utf-8", "ignore"))
        return "\n".join(partes)
    if caminho.suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(pg.extract_text() for pg in PdfReader(io.BytesIO(dados)).pages)
    return dados.decode("utf-8", "ignore")


@pytest.mark.parametrize("formato", ["xlsx", "json", "zip"])
def test_exportacoes_sem_dados_identificaveis(analise, formato):
    p, f, _ = analise
    arq = exportacao.exportar_conjunto(p, "tudo", formato)
    txt = _todo_texto(arq)
    assert "PARTICIPANTE_001" in txt and "faca de dois gumes" in txt
    for nome in NOMES_REAIS:
        assert nome not in txt, f"{nome} vazou em {arq.name}"


@pytest.mark.parametrize("conjunto", list(exportacao.CONJUNTOS))
def test_cada_conjunto_em_csv(analise, conjunto):
    p, *_ = analise
    arq = exportacao.exportar_conjunto(p, conjunto, "csv")
    txt = arq.read_text(encoding="utf-8-sig")
    assert txt.splitlines()[0]
    for nome in NOMES_REAIS:
        assert nome not in txt


@pytest.mark.parametrize("formato", ["docx", "pdf", "md", "html"])
def test_exportar_relatorio(analise, formato):
    p, f, _ = analise
    rid = rel.gerar_rascunho(p, "final")
    arq = exportacao.exportar_relatorio(p, rid, formato)
    txt = _todo_texto(arq) if formato in ("docx", "pdf") else arq.read_text(encoding="utf-8")
    assert "faca de dois gumes" in txt.replace("\n", " ") and "PARTICIPANTE_001" in txt
    for nome in NOMES_REAIS:
        assert nome not in txt
    assert p.con.execute("SELECT COUNT(*) FROM exportacoes").fetchone()[0] >= 1
    assert p.con.execute("SELECT COUNT(*) FROM historico WHERE operacao='exportar'").fetchone()[0] >= 1


def test_exportacao_protegida_do_mapeamento(analise):
    p, *_ = analise
    with pytest.raises(ErroValidacao):
        exportacao.exportar_mapeamento_protegido(p, SENHA, SENHA, "teste")  # senhas iguais
    arq = exportacao.exportar_mapeamento_protegido(p, "demonstracao-atena", "exportacao-123", "exigência do CEP")
    bruto = arq.read_bytes()
    assert b"Marina" not in bruto
    dados = exportacao.abrir_pacote_protegido(bruto, "exportacao-123")
    assert any(i["codigo"] == "PARTICIPANTE_001" and i["valor"] == "Marina Quaresma" for i in dados["identidades"])
    with pytest.raises(ErroValidacao):
        exportacao.abrir_pacote_protegido(bruto, "senha-errada-1")
    assert p.con.execute("SELECT protegida FROM exportacoes ORDER BY id DESC").fetchone()[0] == 1
