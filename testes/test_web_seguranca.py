import re

from atena.servicos import demo
from atena.web.app import criar_app
from servicos_auxiliares import BASE, Cliente


def test_sem_token_nao_acessa():
    app = criar_app("segredo", testes=True)
    c = app.test_client()
    assert c.get("/", base_url=BASE).status_code == 302
    assert c.get("/entrar?t=errado", base_url=BASE).headers["Location"].endswith("/acesso")
    assert c.get("/", base_url=BASE).headers["Location"].endswith("/acesso")
    assert c.get("/entrar?t=segredo", base_url=BASE).status_code == 302
    assert c.get("/", base_url=BASE).status_code == 200


def test_host_externo_bloqueado():
    app = criar_app("t", testes=True)
    c = app.test_client()
    assert c.get("/entrar?t=t", base_url="http://atacante.exemplo").status_code == 403


def test_post_sem_csrf_recusado():
    cli = Cliente(criar_app("tok", testes=True))
    r = cli.c.post("/projetos/novo", data={"nome": "X", "senha": "12345678", "senha2": "12345678"}, base_url=BASE)
    assert r.status_code == 302
    from atena.servicos import projetos
    assert projetos.listar() == []
    r = cli.c.post("/api/p/abc/termos", json={}, base_url=BASE, headers={"X-CSRF": "falso"})
    assert r.status_code == 400


def test_cabecalhos_de_seguranca():
    cli = Cliente(criar_app("tok", testes=True))
    r = cli.c.get("/", base_url=BASE)
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"


def test_todas_as_paginas_abrem():
    cli = Cliente(criar_app("tok", testes=True))
    uid, _ = demo.criar_projeto_demo()
    cli.post(f"/p/{uid}/anonimizacao/1")
    paginas = ["/", "/configuracoes", f"/p/{uid}/", f"/p/{uid}/projeto", f"/p/{uid}/cofre", f"/p/{uid}/participantes",
               f"/p/{uid}/participantes/1", f"/p/{uid}/documentos", f"/p/{uid}/documentos/1", f"/p/{uid}/documentos/1/protegido",
               f"/p/{uid}/anonimizacao", f"/p/{uid}/anonimizacao/1", f"/p/{uid}/anonimizacao/2",
               f"/p/{uid}/anonimizacao/sessao/1/previa", f"/p/{uid}/entrevistas", f"/p/{uid}/editor", f"/p/{uid}/analise",
               f"/p/{uid}/codigos", f"/p/{uid}/temas", f"/p/{uid}/revisao", f"/p/{uid}/definicao", f"/p/{uid}/notas",
               f"/p/{uid}/decisoes", f"/p/{uid}/relatorios", f"/p/{uid}/historico", f"/p/{uid}/ia", f"/p/{uid}/backup",
               f"/p/{uid}/integridade", f"/p/{uid}/fragmentos", f"/p/{uid}/fragmentos/revisar", f"/p/{uid}/codigos/matriz"]
    paginas += [f"/p/{uid}/visualizacoes?aba={a}" for a in ("mapa", "frequencia", "matriz", "temas_entrevistas", "distribuicao",
                                                            "comparacao", "linha_tempo", "lexical")]
    for u in paginas:
        r = cli.c.get(u, base_url=BASE)
        assert r.status_code == 200, u
        assert 'name="_csrf" value=""' not in r.get_data(as_text=True), f"CSRF vazio em {u}"


def test_cofre_bloqueado_redireciona_para_desbloqueio():
    cli = Cliente(criar_app("tok", testes=True))
    uid, _ = demo.criar_projeto_demo()
    from atena.cofre import cofres
    cofres.bloquear()
    r = cli.c.get(f"/p/{uid}/anonimizacao/1", base_url=BASE)
    assert r.status_code == 302 and "/cofre" in r.headers["Location"]
    r = cli.c.post(f"/p/{uid}/cofre", data={"_csrf": cli.csrf, "senha": demo.SENHA_DEMO, "proximo": f"/p/{uid}/anonimizacao/1"}, base_url=BASE)
    assert r.headers["Location"].endswith(f"/p/{uid}/anonimizacao/1")
