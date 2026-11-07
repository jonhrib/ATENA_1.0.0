import sqlite3

import pytest

from atena.servicos import codificacao as cod, documentos, transcricoes as tr
from atena.servicos.projetos import ErroValidacao

TEXTO = ("ENTREVISTADOR - 10:00:\nComo foi?\n\nPARTICIPANTE_001 - 10:01:\nNo começo eu quase desisti do curso. "
         "Depois a empresa júnior me deu motivo para ficar. Também trabalhava à noite.\n")


@pytest.fixture
def doc(projeto):
    return documentos.criar_manual(projeto, TEXTO, ja_anonimizado=True)


def _sel(t, trecho):
    i = t.index(trecho)
    return i, i + len(trecho)


def test_varios_codigos_por_fragmento_e_varios_fragmentos_por_codigo(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    f1 = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "quase desisti do curso"), novo_codigo={"nome": "Desejo de desistir"})
    c1 = f1["codigos"][0]["id"]
    f2 = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "me deu motivo para ficar"), codigo_ids=[c1],
                               novo_codigo={"nome": "EJ como motivo de permanência"})
    assert len(f2["codigos"]) == 2
    ev = cod.evidencias(projeto, c1)
    assert {e["texto"] for e in ev} == {"quase desisti do curso", "me deu motivo para ficar"}
    assert all(e["documento_codigo"] == doc["codigo"] for e in ev)
    assert cod.obter_codigo(projeto, c1)["n_fragmentos"] == 2


def test_selecao_desatualizada_e_recusada(projeto, doc):
    with pytest.raises(ErroValidacao):
        cod.criar_fragmento(projeto, doc["id"], 0, 10, texto_esperado="texto que não está lá")


def test_edicao_realoca_e_sinaliza_fragmentos(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    fa = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "quase desisti do curso"), novo_codigo={"nome": "A"})
    fb = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "trabalhava à noite"), novo_codigo={"nome": "B"})
    novo = t.replace("Como foi?", "Como foi a sua trajetória?").replace("trabalhava à noite", "trabalhava de noite")
    r = tr.salvar_edicao(projeto, doc["id"], novo, "correção", versao_base=1)
    assert r["numero"] == 2 and r["ok"] == 1 and r["inconsistente"] == 1
    a, b = cod.obter_fragmento(projeto, fa["id"]), cod.obter_fragmento(projeto, fb["id"])
    v2 = tr.versao_atual(projeto, doc["id"])["texto"]
    assert a["situacao"] == "ok" and v2[a["inicio"]:a["fim"]] == "quase desisti do curso"
    assert b["situacao"] == "inconsistente" and b["texto"] == "trabalhava à noite"   # texto original preservado
    cod.resolver_situacao(projeto, fb["id"], "confirmar", justificativa="mesmo sentido")
    b2 = cod.obter_fragmento(projeto, fb["id"])
    assert b2["situacao"] == "ok" and b2["texto"].startswith("trabalhava de noite")


def test_trecho_deslocado_reencontrado(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    f = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "empresa júnior"), novo_codigo={"nome": "EJ"})
    novo = t.replace("Depois a empresa júnior me deu motivo para ficar.", "Depois me deu motivo para ficar.") + "\nA empresa júnior ajudou.\n"
    tr.salvar_edicao(projeto, doc["id"], novo, "reorganização")
    f2 = cod.obter_fragmento(projeto, f["id"])
    v = tr.versao_atual(projeto, doc["id"])["texto"]
    assert f2["situacao"] == "realocado" and v[f2["inicio"]:f2["fim"]] == "empresa júnior"


def test_versoes_imutaveis_restauracao_e_comparacao(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    tr.salvar_edicao(projeto, doc["id"], t + "\nNova linha.\n", "acréscimo")
    with pytest.raises(sqlite3.DatabaseError):
        projeto.con.execute("UPDATE transcricao_versoes SET texto = 'x'")
    vs = tr.versoes(projeto, doc["id"])
    comp = tr.comparar(projeto, vs[1]["id"], vs[0]["id"])
    assert any(l["tipo"] == "inserida" and "Nova linha" in l["texto"] for l in comp["linhas"])
    r = tr.restaurar(projeto, doc["id"], vs[1]["id"], "voltar ao original")
    assert r["numero"] == 3 and tr.versao_atual(projeto, doc["id"])["texto"] == t
    with pytest.raises(ErroValidacao):
        tr.salvar_edicao(projeto, doc["id"], t + "x", "conflito", versao_base=1)


def test_busca_no_texto(projeto, doc):
    assert len(tr.buscar(projeto, doc["id"], "EMPRESA")) == 1
    assert tr.buscar(projeto, doc["id"], "EMPRESA", diferenciar=True) == []


def test_codigos_editar_excluir_restaurar_mesclar(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    a = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "quase desisti"), novo_codigo={"nome": "Desistência"})["codigos"][0]["id"]
    b = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "trabalhava à noite"), novo_codigo={"nome": "Trabalho"})["codigos"][0]["id"]
    with pytest.raises(ErroValidacao):
        cod.criar_codigo(projeto, "desistência")
    cod.editar_codigo(projeto, a, descricao="Relatos de vontade de sair", motivo="refino")
    cod.excluir_codigo(projeto, b, "teste")
    assert cod.obter_codigo(projeto, b)["situacao"] == "excluido"
    cod.restaurar_codigo(projeto, b, "teste")
    cod.mesclar_codigos(projeto, [b], a, "mesmo significado")
    assert cod.obter_codigo(projeto, b)["situacao"] == "mesclado"
    assert cod.obter_codigo(projeto, a)["n_fragmentos"] == 2
    ops = [r[0] for r in projeto.con.execute("SELECT operacao FROM historico WHERE entidade='codigo' ORDER BY id")]
    assert ops == ["criar", "criar", "editar", "excluir", "restaurar", "mesclar"]


def test_remover_codificacao_e_fragmento(projeto, doc):
    t = tr.versao_atual(projeto, doc["id"])["texto"]
    f = cod.codificar_selecao(projeto, doc["id"], *_sel(t, "quase desisti"), novo_codigo={"nome": "X"})
    cod.remover_codificacao(projeto, f["codigos"][0]["codificacao_id"], "engano")
    assert cod.obter_fragmento(projeto, f["id"])["codigos"] == []
    cod.desativar_fragmento(projeto, f["id"], "não é relevante")
    assert cod.fragmentos_documento(projeto, doc["id"]) == []
    assert projeto.con.execute("SELECT COUNT(*) FROM fragmentos").fetchone()[0] == 1  # nada é apagado
