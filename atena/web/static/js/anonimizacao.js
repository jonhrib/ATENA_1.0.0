/* Revisão da anonimização: decisões por grupo/ocorrência, inclusão manual e localização no texto. */
(function () {
  "use strict";
  const { api, aviso, selecionarIntervalo, barraSelecao } = window.ATENA;
  const script = document.getElementById("js-anon");
  const URL_DECIDIR = script.dataset.urlDecidir;
  const texto = document.getElementById("texto-anon");
  const recarregar = () => location.reload();

  function codigoEscolhido(caixa) {
    const sel = caixa.querySelector(".sel-cod");
    if (!sel) return { codigo: null, novo: false };
    if (sel.value === "__digitar") {
      const c = (prompt("Código de substituição (ex.: PESSOA_007, ORIENTADORA):") || "").trim().toUpperCase();
      if (!c) return null;
      return { codigo: c, novo: false };
    }
    return { codigo: sel.value || null, novo: !sel.value };
  }

  async function decidir(itens, decisao, caixa, termo) {
    const cod = decisao === "anonimizar" ? codigoEscolhido(caixa) : { codigo: null, novo: false };
    if (cod === null) return;
    try {
      await api(URL_DECIDIR, {
        itens, decisao, categoria: caixa.querySelector(".sel-cat")?.value, codigo: cod.codigo, novo_codigo: cod.novo,
        excecao: !!caixa.querySelector(".chk-excecao")?.checked, termo,
      });
      recarregar();
    } catch (e) { aviso(e.message, "erro"); }
  }

  document.querySelectorAll(".grupo-anon").forEach((g) => {
    const itens = g.dataset.itens.split(",").map(Number);
    g.querySelectorAll(".acao-anon").forEach((b) => b.addEventListener("click", () => decidir(itens, b.dataset.decisao, g, g.dataset.termo)));
    g.querySelectorAll(".acao-item").forEach((b) => b.addEventListener("click", () => {
      const id = Number(b.closest("[data-item]").dataset.item);
      decidir([id], b.dataset.decisao, g, g.dataset.termo);
    }));
  });

  document.addEventListener("click", (e) => {
    const a = e.target.closest(".ir-item");
    if (a) { e.preventDefault(); selecionarIntervalo(texto, +a.dataset.i, +a.dataset.f); }
    const aj = e.target.closest(".ajustar-item");
    if (aj) {
      e.preventDefault();
      const s = window.ATENA.selecaoEm(texto);
      if (!s) return aviso("Selecione no texto o trecho correto e clique de novo em “corrigir trecho”.", "info");
      api(aj.dataset.url, { inicio: s.inicio, fim: s.fim }).then(recarregar).catch((err) => aviso(err.message, "erro"));
    }
    const seg = e.target.closest(".seg[data-item]");
    if (seg && window.getSelection().isCollapsed) {
      const alvo = document.getElementById("item-" + seg.dataset.item);
      if (alvo) {
        const det = alvo.closest("details");
        if (det) det.open = true;
        alvo.scrollIntoView({ block: "center", behavior: "smooth" });
        const cx = alvo.closest(".grupo-anon") || alvo;
        cx.style.outline = "2px solid var(--roxo)";
        setTimeout(() => (cx.style.outline = ""), 1600);
      }
    }
  });

  const filtro = document.getElementById("filtro-decisao");
  filtro?.addEventListener("change", () => {
    document.querySelectorAll(".grupo-anon").forEach((g) => {
      g.classList.toggle("oculto", !!filtro.value && g.dataset.decisao !== filtro.value && !(filtro.value === "pendente" && g.dataset.decisao === "mista"));
    });
  });

  const dlg = document.getElementById("dlg-manual");
  let sel = null;
  barraSelecao(texto, [{
    rotulo: "Marcar como dado pessoal",
    acao: (s) => { sel = s; document.getElementById("manual-trecho").textContent = s.texto; dlg.returnValue = ""; dlg.showModal(); },
  }]);
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "ok" || !sel) return;
    try {
      await api(texto.dataset.urlManual, { inicio: sel.inicio, fim: sel.fim, categoria: document.getElementById("manual-cat").value,
        todas: document.getElementById("manual-todas").checked });
      recarregar();
    } catch (e) { aviso(e.message, "erro"); }
  });
})();
