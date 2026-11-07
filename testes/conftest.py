"""Configuração dos testes: cada teste usa uma pasta de dados temporária e dados 100% fictícios."""
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["ATENA_KDF_RAPIDO"] = "1"

SENHA = "senha-de-teste-123"


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("ATENA_DADOS", str(tmp_path / "dados"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from atena.cofre import cofres
    cofres.bloquear()
    yield tmp_path
    cofres.bloquear()


@pytest.fixture
def projeto():
    from atena.servicos import projetos
    uid, codigo = projetos.criar({"nome": "Projeto de teste (fictício)", "pergunta": "Pergunta fictícia?"}, SENHA,
                                 termos_protegidos=["Universidade do Vale Fictício", "Fictícia Jr."])
    p = projetos.Projeto(uid)
    p.codigo_recuperacao = codigo
    yield p
    p.fechar()


@pytest.fixture
def projeto_demo():
    """Projeto de demonstração (3 entrevistas fictícias importadas, ainda não anonimizadas)."""
    from atena.servicos import demo, projetos
    uid, _ = demo.criar_projeto_demo()
    p = projetos.Projeto(uid)
    yield p
    p.fechar()


def anonimizar_tudo(p, did):
    """Simula a revisão humana: aprova os itens de confiança alta e decide os demais."""
    from atena.servicos import anonimizacao as an
    s = an.iniciar_sessao(p, did)
    an.aprovar_sugestoes_altas(p, s["id"])
    for i in an.itens(p, s["id"]):
        if i["decisao"] == "pendente":
            an.decidir(p, [i["id"]], "anonimizar" if i["categoria"] in ("PESSOA", "LOCAL", "DOCUMENTO", "MATRICULA") else "rejeitar",
                       categoria=i["categoria"])
    return an.aplicar(p, s["id"], "revisão simulada no teste")
