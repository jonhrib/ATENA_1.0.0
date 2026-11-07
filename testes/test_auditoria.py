import sqlite3

import pytest

from atena.auditoria import consultar, verificar_cadeia
from atena.servicos import codificacao as cod, painel, projetos, restauracao


def test_historico_append_only(projeto):
    with pytest.raises(sqlite3.DatabaseError):
        projeto.con.execute("UPDATE historico SET operacao = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        projeto.con.execute("DELETE FROM historico")


def test_cadeia_de_hashes_detecta_adulteracao(projeto):
    cod.criar_codigo(projeto, "Código A")
    cod.criar_codigo(projeto, "Código B")
    assert verificar_cadeia(projeto.con)["integra"]
    # simula adulteração externa (alguém desliga o gatilho e altera o banco com outra ferramenta)
    projeto.con.execute("DROP TRIGGER historico_sem_update")
    projeto.con.execute("UPDATE historico SET justificativa = 'adulterado' WHERE id = 2")
    r = verificar_cadeia(projeto.con)
    assert not r["integra"] and r["quebra_em"] == 2
    assert not painel.integridade(projeto)["ok"]


def test_registros_tem_campos_obrigatorios(projeto):
    c = cod.criar_codigo(projeto, "Código", justificativa="porque sim")
    cod.editar_codigo(projeto, c["id"], nome="Código renomeado", motivo="clareza")
    h, total = consultar(projeto.con, entidade="codigo", entidade_id=str(c["id"]))
    assert total == 2
    ed = h[0]
    assert ed["operacao"] == "editar" and ed["origem"] == "pesquisador" and ed["momento"]
    assert ed["antes_obj"] == {"nome": "Código"} and ed["depois_obj"] == {"nome": "Código renomeado"} and ed["justificativa"] == "clareza"
    assert consultar(projeto.con, texto="renomeado")[1] >= 1
    assert consultar(projeto.con, origem="ia")[1] == 0


def test_restauracao_a_partir_do_historico(projeto):
    c = cod.criar_codigo(projeto, "Original", descricao="d1")
    cod.editar_codigo(projeto, c["id"], nome="Alterado", descricao="d2")
    hid = projeto.con.execute("SELECT id FROM historico WHERE operacao='editar'").fetchone()[0]
    h = consultar(projeto.con, entidade="codigo")[0][0]
    assert restauracao.restauravel(h | {"antes": h["antes_obj"]})
    restauracao.restaurar(projeto, hid, "voltar atrás")
    assert cod.obter_codigo(projeto, c["id"])["nome"] == "Original"
    assert projeto.con.execute("SELECT COUNT(*) FROM historico WHERE entidade='codigo'").fetchone()[0] == 3
    cod.excluir_codigo(projeto, c["id"], "x")
    hid = projeto.con.execute("SELECT id FROM historico WHERE operacao='excluir'").fetchone()[0]
    restauracao.restaurar(projeto, hid, "engano")
    assert cod.obter_codigo(projeto, c["id"])["situacao"] == "ativo"


def test_projeto_editar_arquivar(projeto):
    projetos.atualizar(projeto, {"objetivo": "Novo objetivo"}, "ajuste")
    projetos.alterar_situacao(projeto, "arquivado", "concluído")
    assert projeto.dados()["situacao"] == "arquivado" and projeto.dados()["objetivo"] == "Novo objetivo"
    assert [p["uid"] for p in projetos.listar()] == [projeto.uid]
