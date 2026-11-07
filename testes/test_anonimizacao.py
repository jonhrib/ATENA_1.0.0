import pytest

from atena.servicos import anonimizacao as an, documentos, participantes, projetos, protegido, transcricoes
from atena.servicos.projetos import ErroValidacao
from conftest import anonimizar_tudo


def test_fluxo_completo_e_original_preservado(projeto_demo):
    p = projeto_demo
    d = documentos.listar(p)[0]
    original_rev = protegido.ler_texto(p, d["id"])["texto"]
    r = anonimizar_tudo(p, d["id"])
    v = transcricoes.versao_atual(p, d["id"])["texto"]
    for nome in ("Marina", "Quaresma", "Otávio", "Prates", "marina.quaresma@exemplo.com.br", "98888-1234", "Pesquisadora Demo", "Vila Serena"):
        assert nome not in v, nome
    assert "PARTICIPANTE_001" in v and "ENTREVISTADOR" in v
    assert "Universidade do Vale Fictício" in v and "Fictícia Jr." in v  # termos protegidos
    assert protegido.ler_texto(p, d["id"])["texto"] == original_rev       # revisado intacto
    assert documentos.obter(p, d["id"])["situacao"] == "anonimizado"
    assert r["residuos"] == []
    hist = p.con.execute("SELECT depois FROM historico WHERE operacao='aplicar'").fetchone()[0]
    assert "Marina" not in hist  # o histórico não guarda valores identificáveis


def test_aplicar_exige_todas_as_decisoes(projeto_demo):
    p = projeto_demo
    s = an.iniciar_sessao(p, documentos.listar(p)[0]["id"])
    with pytest.raises(ErroValidacao):
        an.aplicar(p, s["id"])
    assert transcricoes.versao_atual(p, s["documento_id"]) is None


def test_mesmo_participante_mesmo_codigo_em_varios_documentos(projeto):
    pa = participantes.criar(projeto, {}, nome_real="Rosana Ficticia")
    d1 = documentos.criar_manual(projeto, "A Rosana Ficticia falou. Depois Rosana Ficticia voltou.", participante_id=pa["id"])
    d2 = documentos.criar_manual(projeto, "Encontrei Rosana Ficticia e Tomás Ribeiro.", participante_id=pa["id"])
    anonimizar_tudo(projeto, d1["id"]); anonimizar_tudo(projeto, d2["id"])
    t1, t2 = (transcricoes.versao_atual(projeto, x["id"])["texto"] for x in (d1, d2))
    assert t1.count(pa["codigo"]) == 2 and pa["codigo"] in t2
    assert "PESSOA_001" in t2 and "Tomás" not in t2


def test_terceiro_citado_recebe_mesmo_codigo_entre_documentos(projeto):
    d1 = documentos.criar_manual(projeto, "O professor Tomás Ribeiro ajudou.")
    d2 = documentos.criar_manual(projeto, "Falei com Tomás Ribeiro ontem.")
    anonimizar_tudo(projeto, d1["id"]); anonimizar_tudo(projeto, d2["id"])
    assert "PESSOA_001" in transcricoes.versao_atual(projeto, d2["id"])["texto"]
    assert "PESSOA_002" not in transcricoes.versao_atual(projeto, d2["id"])["texto"]


def test_manual_rejeitar_manter_e_excecao(projeto):
    d = documentos.criar_manual(projeto, "Meu apelido é Tigrão e eu moro perto do Mercado Azul. Clara disse que sim.")
    s = an.iniciar_sessao(projeto, d["id"])
    texto = protegido.ler_texto(projeto, d["id"])["texto"]
    i = texto.index("Tigrão")
    ids = an.adicionar_manual(projeto, s["id"], i, i + len("Tigrão"), "PESSOA")
    assert len(ids) == 1
    for it in an.itens(projeto, s["id"]):
        if it["trecho"] == "Tigrão":
            an.decidir(projeto, [it["id"]], "anonimizar", codigo="APELIDO_001")
        elif it["trecho"] == "Clara":
            an.decidir(projeto, [it["id"]], "manter", observacao="nome de pessoa pública")
        else:
            an.decidir(projeto, [it["id"]], "rejeitar")
    an.aplicar(projeto, s["id"])
    t = transcricoes.versao_atual(projeto, d["id"])["texto"]
    assert "APELIDO_001" in t and "Tigrão" not in t and "Clara" in t


def test_termo_protegido_nao_pode_ser_anonimizado(projeto):
    d = documentos.criar_manual(projeto, "Entrei na Fictícia Jr. no segundo ano.")
    s = an.iniciar_sessao(projeto, d["id"])
    texto = protegido.ler_texto(projeto, d["id"])["texto"]
    i = texto.index("Fictícia")
    with pytest.raises(ErroValidacao):
        an.adicionar_manual(projeto, s["id"], i, i + 8, "INSTITUICAO")


def test_ajuste_de_trecho(projeto):
    d = documentos.criar_manual(projeto, "Conversei com a professora Helena Barros Lima.")
    s = an.iniciar_sessao(projeto, d["id"])
    it = [x for x in an.itens(projeto, s["id"]) if "Helena" in x["trecho"]][0]
    texto = protegido.ler_texto(projeto, d["id"])["texto"]
    i = texto.index("Helena")
    an.ajustar_trecho(projeto, it["id"], i, i + len("Helena Barros Lima"))
    it2 = [x for x in an.itens(projeto, s["id"]) if x["id"] == it["id"]][0]
    assert it2["trecho"] == "Helena Barros Lima" and it2["decisao"] == "pendente"


def test_reanonimizacao_realoca_fragmentos(projeto_demo):
    from atena.servicos import codificacao as cod
    p = projeto_demo
    d = documentos.listar(p)[0]
    anonimizar_tudo(p, d["id"])
    t = transcricoes.versao_atual(p, d["id"])["texto"]
    i = t.index("faca de dois gumes")
    f = cod.codificar_selecao(p, d["id"], i, i + 18, novo_codigo={"nome": "Ambivalência"})
    rev = protegido.ler_texto(p, d["id"])["texto"].replace("Puxada.", "Puxada demais.")
    documentos.salvar_revisao_protegida(p, d["id"], rev, "correção")
    anonimizar_tudo(p, d["id"])
    v = transcricoes.versao_atual(p, d["id"])
    assert v["numero"] == 2 and v["origem"] == "reanonimizacao" and "Puxada demais." in v["texto"]
    f2 = cod.obter_fragmento(p, f["id"])
    assert f2["situacao"] == "ok" and v["texto"][f2["inicio"]:f2["fim"]] == "faca de dois gumes"
