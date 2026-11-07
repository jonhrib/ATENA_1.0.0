"""Teste de ponta a ponta no navegador (Chromium/Playwright): seleção com o mouse, diálogos e mapa.

É ignorado automaticamente se o Playwright/Chromium não estiver instalado.
Defina ATENA_CAPTURAS=<pasta> para salvar capturas de tela (usadas no manual).
"""
import os
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

SELECIONAR = """([alvo, trecho]) => {
  const c = document.querySelector(alvo);
  const w = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) { const i = n.data.indexOf(trecho); if (i >= 0) {
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + trecho.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    c.dispatchEvent(new MouseEvent('mouseup', {bubbles: true})); return true; } }
  return false; }"""


@pytest.fixture
def servidor():
    from werkzeug.serving import make_server
    from atena.web.app import criar_app
    app = criar_app("token-e2e")
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def foto(pg, nome):
    pasta = os.environ.get("ATENA_CAPTURAS")
    if pasta:
        os.makedirs(pasta, exist_ok=True)
        pg.screenshot(path=os.path.join(pasta, nome + ".png"))


def test_fluxo_no_navegador(servidor):
    with pw.sync_playwright() as p:
        try:
            nav = p.chromium.launch()
        except Exception as exc:  # navegador não instalado
            pytest.skip(f"Chromium indisponível: {exc}")
        pg = nav.new_page(viewport={"width": 1440, "height": 900})
        erros = []
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
        pg.goto(servidor + "/entrar?t=token-e2e")
        foto(pg, "01_inicio")
        pg.click("text=Explorar um projeto de demonstração")
        pg.check("input[name=confirmo]")
        pg.click("text=Continuar para o projeto")
        foto(pg, "02_painel")

        # ---- anonimização
        pg.click("nav >> text=Anonimização")
        pg.click("tr:has-text('DOC_001') >> text=Iniciar revisão")
        pg.click("text=Detectar e iniciar revisão")
        pg.click("text=Aprovar itens de confiança alta")
        pg.wait_for_selector("text=item(ns) de confiança alta aprovados")
        # marca manualmente um trecho não detectado
        assert pg.evaluate(SELECIONAR, ["#texto-anon", "numa loja"])
        pg.click(".barra-selecao >> text=Marcar como dado pessoal", timeout=5000)
        pg.select_option("#manual-cat", "LOCAL")
        pg.click("#dlg-manual button[value=ok]")
        pg.wait_for_load_state()
        foto(pg, "03_anonimizacao")
        for _ in range(10):
            pend = pg.locator(".grupo-anon[data-decisao='pendente'], .grupo-anon[data-decisao='mista']")
            if pend.count() == 0:
                break
            pend.first.locator(".acao-anon[data-decisao='anonimizar']").click()
            pg.wait_for_load_state(); pg.wait_for_timeout(150)
        pg.click("button:has-text('Aplicar anonimização')")
        pg.check("#dlg-aplicar input[name=confirmo]")
        pg.click("#dlg-aplicar >> button:has-text('Aplicar')")
        pg.wait_for_load_state()
        assert "Anonimização aplicada" in pg.inner_text("#mensagens")

        # ---- editor: selecionar com o mouse e codificar
        pg.click("text=Abrir no editor")
        texto = pg.inner_text("#transcricao")
        assert "Marina" not in texto and "PARTICIPANTE_001" in texto and "LOCAL_" in texto
        assert pg.evaluate(SELECIONAR, ["#transcricao", "quase desisti"])
        pg.click(".barra-selecao >> text=Codificar", timeout=5000)
        pg.fill("#cod-novo-nome", "Vontade de desistir")
        pg.fill("#cod-novo-desc", "Relatos de quase desistência")
        pg.click("#cod-ok")
        pg.wait_for_selector(".seg.f")
        pg.wait_for_selector("#painel-fragmento >> text=Vontade de desistir")
        foto(pg, "04_editor")
        # segundo código no mesmo fragmento
        pg.click("#painel-fragmento >> text=+ Código")
        pg.fill("#cod-novo-nome", "Dificuldade acadêmica inicial")
        pg.click("#cod-ok")
        pg.wait_for_selector("#painel-fragmento >> text=Dificuldade acadêmica inicial")

        # ---- código: evidências rastreáveis
        pg.click("nav >> text=Códigos")
        pg.click("text=Vontade de desistir")
        assert "quase desisti" in pg.inner_text("main") and "PARTICIPANTE_001" in pg.inner_text("main")

        # ---- tema e mapa
        pg.click("nav >> text=Temas")
        pg.click("button:has-text('Novo tema')")
        pg.fill("#dlg-tema input[name=nome]", "Trajetórias de quase desistência")
        pg.check("#lista-cod-tema label:has-text('Vontade de desistir') input")
        pg.fill("#dlg-tema textarea[name=justificativa]", "padrão compartilhado")
        pg.click("#dlg-tema >> text=Criar tema")
        pg.wait_for_selector("#mapa canvas")
        pg.wait_for_timeout(600)
        foto(pg, "05_mapa")

        # ---- relatório
        pg.click("nav >> text=Relatórios")
        pg.click("button:has-text('Gerar')")
        assert "Trajetórias de quase desistência" in pg.inner_text("#previa")
        foto(pg, "06_relatorio")
        pg.click("nav >> text=Histórico")
        assert "cadeia íntegra" in pg.inner_text("main")
        foto(pg, "07_historico")
        pg.click("nav a:has-text('Análise temática')")
        pg.wait_for_selector("h1:has-text('Braun e Clarke')")
        foto(pg, "08_analise_tematica")
        pg.click("nav >> text=Visualizações")
        pg.click("text=Matriz por participante")
        pg.wait_for_selector("table.calor")
        foto(pg, "09_visualizacoes")
        pg.click("nav >> text=Configurações")
        pg.wait_for_selector("h1:has-text('Configurações')")
        foto(pg, "10_configuracoes")
        nav.close()
        assert not erros, erros
