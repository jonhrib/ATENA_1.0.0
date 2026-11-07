/* Editor de relatórios: inserção de citações vinculadas e prévia com verificação. */
(function () {
  "use strict";
  const { api, aviso, esc } = window.ATENA;
  const ta = document.getElementById("conteudo");
  const URL = document.getElementById("js-rel").dataset.urlPrevia;
  document.querySelectorAll(".inserir-frag").forEach((b) => b.addEventListener("click", () => {
    const tok = `\n\n[[frag:${b.dataset.id}]]\n\n`;
    const i = ta.selectionStart ?? ta.value.length;
    ta.value = ta.value.slice(0, i) + tok + ta.value.slice(ta.selectionEnd ?? i);
    ta.selectionStart = ta.selectionEnd = i + tok.length;
    b.closest("dialog").close();
    ta.focus();
    previa();
  }));
  async function previa() {
    try {
      const r = await api(URL, { conteudo: ta.value });
      document.getElementById("previa").innerHTML = r.html;
      const erros = r.verificacao.filter((v) => v.gravidade === "erro");
      if (erros.length) aviso(erros.map((e) => e.texto).join(" · "), "alerta");
    } catch (e) { aviso(e.message, "erro"); }
  }
  document.getElementById("btn-previa").addEventListener("click", previa);
  let t;
  ta.addEventListener("input", () => { clearTimeout(t); t = setTimeout(previa, 1200); });
})();
