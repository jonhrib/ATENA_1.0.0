/* ATENA — interações da interface (sem dependências externas). */
(function () {
  "use strict";
  const CSRF = document.querySelector('meta[name="csrf"]')?.content || "";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(url, dados, metodo) {
    const r = await fetch(url, {
      method: metodo || (dados === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", "X-CSRF": CSRF },
      body: dados === undefined ? undefined : JSON.stringify(dados),
      credentials: "same-origin",
    });
    let j = {};
    try { j = await r.json(); } catch (e) { j = { erro: "Resposta inválida do servidor." }; }
    if (!r.ok || j.erro) {
      if (j.cofre_bloqueado) { aviso(j.erro + " Abra o cofre pelo botão no topo da página.", "alerta"); }
      throw new Error(j.erro || "Falha na operação.");
    }
    return j;
  }
  window.ATENA = { api, esc };

  function aviso(msg, tipo = "info") {
    const box = $("#mensagens");
    if (!box) return alert(msg);
    const d = document.createElement("div");
    d.className = "aviso " + tipo;
    d.innerHTML = `<div>${esc(msg)}</div><button class="fechar" type="button">×</button>`;
    box.prepend(d);
    if (tipo === "sucesso") setTimeout(() => d.remove(), 5000);
  }
  window.ATENA.aviso = aviso;

  // ---------------------------------------------------------------- mensagens
  document.addEventListener("click", (e) => {
    if (e.target.matches(".mensagens .fechar")) e.target.closest(".aviso").remove();
  });
  $$(".mensagens .aviso.sucesso").forEach((d) => setTimeout(() => d.remove(), 7000));

  // ---------------------------------------------------------------- confirmações
  const dlg = $("#dlg-confirmar");
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!f.matches(".form-confirmar") || f.dataset.confirmado === "1") return;
    e.preventDefault();
    $("#dlg-confirmar-texto").textContent = f.dataset.confirmar || "Confirmar?";
    const precisa = f.dataset.justificativa === "1";
    $("#dlg-confirmar-just").classList.toggle("oculto", !precisa);
    const ta = $("#dlg-justificativa");
    ta.value = "";
    ta.placeholder = f.dataset.placeholder || "";
    dlg.returnValue = "";
    dlg.showModal();
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return;
      if (precisa) {
        if (!ta.value.trim()) { aviso("A justificativa é obrigatória.", "erro"); return; }
        const campo = f.querySelector('input[name="justificativa"]') || f.querySelector('input[name="motivo"]');
        if (campo) campo.value = ta.value.trim();
      }
      f.dataset.confirmado = "1";
      f.submit();
    };
  });

  // ---------------------------------------------------------------- utilidades de seleção
  function posicaoNoTexto(container, no, off) {
    let el = no.nodeType === 3 ? no.parentElement : no;
    const seg = el && el.closest ? el.closest(".seg") : null;
    if (!seg || !container.contains(seg)) {
      // seleção fora de um segmento: usa o deslocamento total a partir do início do container
      const r = document.createRange();
      r.setStart(container, 0);
      try { r.setEnd(no, off); } catch (e) { return null; }
      return r.toString().length;
    }
    const r = document.createRange();
    r.setStart(seg, 0);
    r.setEnd(no, off);
    return parseInt(seg.dataset.i, 10) + r.toString().length;
  }

  function selecaoEm(container) {
    const s = window.getSelection();
    if (!s || s.rangeCount === 0 || s.isCollapsed) return null;
    const r = s.getRangeAt(0);
    if (!container.contains(r.commonAncestorContainer)) return null;
    let a = posicaoNoTexto(container, r.startContainer, r.startOffset);
    let b = posicaoNoTexto(container, r.endContainer, r.endOffset);
    if (a === null || b === null) return null;
    if (b < a) [a, b] = [b, a];
    const texto = s.toString();
    if (!texto.trim()) return null;
    return { inicio: a, fim: b, texto, rect: r.getBoundingClientRect() };
  }

  function selecionarIntervalo(container, ini, fim) {
    const segs = $$(".seg", container);
    let rIni = null, rFim = null;
    for (const s of segs) {
      const i = +s.dataset.i, f = +s.dataset.f;
      if (rIni === null && ini >= i && ini <= f) rIni = [s, ini - i];
      if (fim >= i && fim <= f) { rFim = [s, fim - i]; if (rIni) break; }
    }
    if (!rIni || !rFim) return;
    const pos = (seg, n) => {
      const w = document.createTreeWalker(seg, NodeFilter.SHOW_TEXT);
      let t, acc = 0;
      while ((t = w.nextNode())) { if (acc + t.length >= n) return [t, n - acc]; acc += t.length; }
      return [seg, seg.childNodes.length];
    };
    const r = document.createRange();
    r.setStart(...pos(...rIni));
    r.setEnd(...pos(...rFim));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    rolarPara(rIni[0]);
  }
  window.ATENA.selecionarIntervalo = selecionarIntervalo;

  // rola apenas o contêiner do texto (scrollIntoView também moveria a página inteira)
  function rolarPara(el) {
    const caixa = el.closest(".transcricao");
    if (!caixa) return el.scrollIntoView({ block: "nearest" });
    const alvo = el.getBoundingClientRect().top - caixa.getBoundingClientRect().top + caixa.scrollTop - caixa.clientHeight / 3;
    caixa.scrollTo({ top: Math.max(0, alvo), behavior: "smooth" });
  }
  window.ATENA.rolarPara = rolarPara;

  function barraSelecao(container, botoes) {
    const barra = document.createElement("div");
    barra.className = "barra-selecao";
    container.parentElement.style.position = "relative";
    container.parentElement.appendChild(barra);
    let atual = null;
    const esconder = () => { barra.style.display = "none"; };
    container.addEventListener("mouseup", () => setTimeout(() => {
      atual = selecaoEm(container);
      if (!atual) return esconder();
      barra.innerHTML = "";
      botoes.forEach((b) => {
        const el = document.createElement("button");
        el.type = "button";
        el.textContent = b.rotulo;
        el.addEventListener("mousedown", (ev) => ev.preventDefault());
        el.addEventListener("click", () => { esconder(); b.acao(atual); });
        barra.appendChild(el);
      });
      const pr = container.parentElement.getBoundingClientRect();
      barra.style.display = "flex";
      barra.style.left = Math.max(4, atual.rect.left - pr.left) + "px";
      barra.style.top = (atual.rect.top - pr.top - 44) + "px";
    }, 10));
    document.addEventListener("mousedown", (e) => { if (!barra.contains(e.target)) esconder(); });
  }
  window.ATENA.barraSelecao = barraSelecao;
  window.ATENA.selecaoEm = selecaoEm;

  // ---------------------------------------------------------------- busca no texto
  function ligarBusca(container) {
    const campo = $("#busca-texto");
    if (!campo) return;
    let ocorr = [], k = -1;
    const info = $("#busca-info");
    const ir = (d) => {
      if (!ocorr.length) return;
      k = (k + d + ocorr.length) % ocorr.length;
      selecionarIntervalo(container, ocorr[k].inicio, ocorr[k].fim);
      info.textContent = `${k + 1} de ${ocorr.length}`;
    };
    const buscar = async () => {
      const q = campo.value;
      if (!q.trim()) { ocorr = []; info.textContent = ""; return; }
      ocorr = await api(campo.dataset.url + "?q=" + encodeURIComponent(q));
      k = -1;
      info.textContent = ocorr.length ? `${ocorr.length} ocorrência(s)` : "nenhuma ocorrência";
      if (window.CSS && CSS.highlights && window.Highlight) {
        const rs = ocorr.map((o) => { selecionarIntervalo(container, o.inicio, o.fim); return window.getSelection().getRangeAt(0).cloneRange(); });
        window.getSelection().removeAllRanges();
        CSS.highlights.set("busca", new Highlight(...rs));
      }
      ir(1);
    };
    campo.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); ocorr.length && campo.value === campo.dataset.ult ? ir(e.shiftKey ? -1 : 1) : (campo.dataset.ult = campo.value, buscar()); } });
    $("#busca-ant")?.addEventListener("click", () => ir(-1));
    $("#busca-prox")?.addEventListener("click", () => ir(1));
  }
  window.ATENA.ligarBusca = ligarBusca;

  // ---------------------------------------------------------------- diálogos (sem JavaScript embutido no HTML, por causa da CSP)
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-abrir]");
    if (a) { e.preventDefault(); document.getElementById(a.dataset.abrir)?.showModal(); }
    const f = e.target.closest("[data-fechar]");
    if (f) { e.preventDefault(); f.closest("dialog")?.close(); }
  });
  // ---------------------------------------------------------------- diálogo genérico
  window.ATENA.abrirDialogo = function (id) { const d = document.getElementById(id); d.returnValue = ""; d.showModal(); return d; };

  // ---------------------------------------------------------------- formulários com campos dinâmicos
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-add-meta]");
    if (!b) return;
    const alvo = document.getElementById(b.dataset.addMeta);
    const div = document.createElement("div");
    div.className = "linha";
    div.innerHTML = '<input type="text" name="meta_chave" placeholder="Campo" style="max-width:220px"><input type="text" name="meta_valor" placeholder="Valor (não identificável)" class="flex1">';
    alvo.appendChild(div);
  });

  // filtro de tabelas/listas
  $$("[data-filtrar]").forEach((campo) => {
    const alvo = document.querySelector(campo.dataset.filtrar);
    campo.addEventListener("input", () => {
      const q = campo.value.toLowerCase();
      $$("[data-texto]", alvo).forEach((el) => { el.classList.toggle("oculto", q && !el.dataset.texto.toLowerCase().includes(q)); });
    });
  });

  // preserva posição de rolagem ao recarregar (anonimização)
  const chaveScroll = "atena-scroll:" + location.pathname;
  const caixa = $("[data-preservar-rolagem]");
  if (caixa) {
    try { const v = sessionStorage.getItem(chaveScroll); if (v) { const o = JSON.parse(v); caixa.scrollTop = o.c; window.scrollTo(0, o.w); } } catch (e) {}
    window.addEventListener("beforeunload", () => { try { sessionStorage.setItem(chaveScroll, JSON.stringify({ c: caixa.scrollTop, w: window.scrollY })); } catch (e) {} });
  }

  // botão de teste de conexão (configurações)
  $("#testar-ia")?.addEventListener("click", async (e) => {
    const alvo = $("#resultado-teste");
    alvo.textContent = "Testando…";
    try {
      const r = await api(e.target.dataset.url, {});
      alvo.innerHTML = (r.ok ? "✅ " : "❌ ") + esc(r.mensagem);
    } catch (err) { alvo.textContent = "❌ " + err.message; }
  });

  // botões de copiar
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copiar]");
    if (!b) return;
    const t = document.querySelector(b.dataset.copiar)?.textContent || "";
    navigator.clipboard?.writeText(t.trim()).then(() => aviso("Copiado.", "sucesso"));
  });
})();
