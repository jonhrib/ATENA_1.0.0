import sqlite3

import pytest

from atena import cofre
from atena.cofre import CofreBloqueado, SenhaIncorreta, cofres
from atena.servicos import participantes, protegido
from conftest import SENHA


def test_cofre_criado_e_desbloqueado(projeto):
    assert projeto.cofre_aberto()
    assert len(projeto.codigo_recuperacao.replace("-", "")) == 24


def test_senha_incorreta_e_correta(projeto):
    with pytest.raises(SenhaIncorreta):
        cofre.abrir(projeto.con_cofre, "senha-errada!!")
    assert cofre.abrir(projeto.con_cofre, SENHA) == projeto.dek()


def test_cofre_bloqueado_impede_acesso(projeto):
    cofres.bloquear(projeto.uid)
    with pytest.raises(CofreBloqueado):
        protegido.identidades(projeto)


def test_chave_nao_fica_em_texto_puro(projeto):
    dek = projeto.dek()
    participantes.criar(projeto, {"Curso": "X"}, nome_real="Fulana Ficticia da Silva")
    for arq in projeto.pasta.glob("*.sqlite*"):
        dados = arq.read_bytes()
        assert dek not in dados, f"DEK em texto puro em {arq.name}"
        assert b"Fulana Ficticia" not in dados, f"nome em texto puro em {arq.name}"


def test_recuperacao_e_troca_de_senha(projeto):
    dek = projeto.dek()
    assert cofre.abrir_com_recuperacao(projeto.con_cofre, projeto.codigo_recuperacao.lower(), "nova-senha-456") == dek
    assert cofre.abrir(projeto.con_cofre, "nova-senha-456") == dek
    cofre.trocar_senha(projeto.con_cofre, "nova-senha-456", "terceira-senha-789")
    with pytest.raises(SenhaIncorreta):
        cofre.abrir(projeto.con_cofre, "nova-senha-456")
    assert cofre.abrir(projeto.con_cofre, "terceira-senha-789") == dek


def test_mapeamento_reversivel_e_consulta_registrada(projeto):
    pa = participantes.criar(projeto, {}, nome_real="Beltrano Ficticio", variantes=["Bel"])
    ids = protegido.identidades(projeto, registrar_consulta=True, motivo="teste")
    assert any(i["codigo"] == pa["codigo"] and i["valor"] == "Beltrano Ficticio" and "Bel" in i["variantes"] for i in ids)
    assert projeto.con.execute("SELECT COUNT(*) FROM historico WHERE operacao = 'consultar_mapeamento'").fetchone()[0] == 1
    assert projeto.con_cofre.execute("SELECT COUNT(*) FROM acessos WHERE operacao = 'consulta_mapeamento'").fetchone()[0] == 1


def test_registro_de_acessos_append_only(projeto):
    with pytest.raises(sqlite3.DatabaseError):
        projeto.con_cofre.execute("DELETE FROM acessos")


def test_codigos_unicos_nunca_reutilizados(projeto):
    a = protegido.criar_identidade(projeto, "PESSOA", "Pessoa Ficticia Um")
    b = protegido.criar_identidade(projeto, "PESSOA", "Pessoa Ficticia Dois")
    assert (a["codigo"], b["codigo"]) == ("PESSOA_001", "PESSOA_002")
    with pytest.raises(Exception):
        protegido.criar_identidade(projeto, "PESSOA", "Outra", codigo="PESSOA_001")
