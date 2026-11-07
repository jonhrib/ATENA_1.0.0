/* Mapa temático interativo (Cytoscape.js, embutido — funciona sem internet). */
(function () {
  "use strict";
  const { api, aviso } = window.ATENA;
  const el = document.getElementById("mapa");
  if (!el || !window.cytoscape) return;
  const dlg = document.getElementById("dlg-mover");
  let cy;

  const estilo = [
    { selector: "node", style: { label: "data(label)", "font-family": "Segoe UI, system-ui, sans-serif", "font-size": 11, "text-wrap": "wrap", "text-max-width": 120 } },
    { selector: 'node[tipo = "codigo"]', style: { "background-color": "data(cor)", width: "mapData(n, 0, 20, 18, 46)", height: "mapData(n, 0, 20, 18, 46)",
      "text-valign": "bottom", "text-margin-y": 4, color: "#1d2731", "border-width": 2, "border-color": "#fff" } },
    { selector: 'node[?em_varios]', style: { "border-color": "#a16207", "border-width": 3 } },
    { selector: 'node[tipo = "tema"]', style: { shape: "round-rectangle", "background-color": "data(cor)", "background-opacity": 0.08, "border-color": "data(cor)",
      "border-width": 2, "text-valign": "top", "text-halign": "center", "font-size": 13, "font-weight": 700, color: "#08363b", padding: 18 } },
    { selector: 'node[tipo = "subtema"]', style: { shape: "round-rectangle", "background-color": "data(cor)", "background-opacity": 0.1, "border-style": "dashed",
      "border-color": "data(cor)", "border-width": 2, "text-valign": "top", "font-size": 12, "font-weight": 600, color: "#3b2a6e", padding: 12 } },
    { selector: 'node[tipo = "area"]', style: { shape: "round-rectangle", "background-color": "#94a3b8", "background-opacity": 0.06, "border-style": "dotted",
      "border-color": "#94a3b8", "text-valign": "top", color: "#5c6977", padding: 14 } },
    { selector: 'node[situacao = "definido"]', style: { "border-width": 3 } },
    { selector: "edge", style: { width: "mapData(peso, 1, 10, 1, 6)", "line-color": "#c4b5fd", opacity: 0.55, "curve-style": "haystack" } },
    { selector: ".alvo", style: { "background-opacity": 0.25, "border-width": 4 } },
  ];

  async function carregar() {
    const cooc = document.getElementById("mostrar-cooc").checked ? "1" : "0";
    const dados = await api(el.dataset.url + "?coocorrencia=" + cooc);
    if (cy) cy.destroy();
    cy = cytoscape({ container: el, elements: dados.elements, style: estilo, wheelSensitivity: 0.25,
      layout: { name: "cose", animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 80, nestingFactor: 0.9, componentSpacing: 60 } });
    let origem = null;
    cy.on("grab", 'node[tipo = "codigo"]', (e) => { origem = e.target.parent().id() || null; });
    cy.on("drag", 'node[tipo = "codigo"]', (e) => {
      cy.nodes(".alvo").removeClass("alvo");
      const alvo = alvoEm(e.target.position(), e.target);
      if (alvo) alvo.addClass("alvo");
    });
    cy.on("free", 'node[tipo = "codigo"]', async (e) => {
      cy.nodes(".alvo").removeClass("alvo");
      const n = e.target;
      const alvo = alvoEm(n.position(), n);
      const destino = alvo ? alvo.id() : null;
      if (destino === origem || (!destino && !origem)) return;
      const de = origem && origem.startsWith("t") ? +origem.slice(1) : null;
      const para = destino && destino.startsWith("t") ? +destino.slice(1) : null;
      if (de === para) return;
      const nomeDest = para ? alvo.data("label") : "Códigos sem tema";
      document.getElementById("mover-texto").textContent = `Mover o código “${n.data("label")}” ${de ? "de “" + cy.getElementById(origem).data("label") + "” " : ""}para “${nomeDest}”?`;
      document.getElementById("mover-just").value = "";
      dlg.returnValue = "";
      dlg.showModal();
      dlg.onclose = async () => {
        if (dlg.returnValue !== "ok") return carregar();
        try {
          await api(el.dataset.urlMover, { codigo_id: n.data("codigo_id"), de, para, justificativa: document.getElementById("mover-just").value });
          aviso("Agrupamento atualizado e registrado no histórico.", "sucesso");
          setTimeout(() => location.reload(), 400);
        } catch (err) { aviso(err.message, "erro"); carregar(); }
      };
    });
    cy.on("dbltap", "node", (e) => {
      const d = e.target.data();
      if (d.tipo === "codigo") location.href = el.dataset.urlCodigo + d.codigo_id;
      else if (d.tema_id) location.href = el.dataset.urlTema + d.tema_id;
    });
  }

  function alvoEm(pos, ignorar) {
    // o subtema mais interno que contém a posição; senão o tema; senão a área sem tema
    const caixas = cy.nodes('[tipo = "subtema"], [tipo = "tema"], [tipo = "area"]').filter((x) => x.id() !== ignorar.id());
    const dentro = caixas.filter((x) => {
      const b = x.boundingBox();
      return pos.x >= b.x1 && pos.x <= b.x2 && pos.y >= b.y1 && pos.y <= b.y2;
    });
    if (!dentro.length) return null;
    const pai = ignorar.parent().length ? ignorar.parent().id() : null;
    const prio = { subtema: 0, tema: 1, area: 2 };
    // o tema de origem cresce junto com o código arrastado; outro tema que contenha a posição tem preferência
    return dentro.sort((a, b) => ((a.id() === pai) - (b.id() === pai)) || (prio[a.data("tipo")] - prio[b.data("tipo")]))[0];
  }

  document.getElementById("mostrar-cooc").addEventListener("change", carregar);
  document.getElementById("mapa-reorganizar").addEventListener("click", () => cy && cy.layout({ name: "cose", animate: true, padding: 30 }).run());
  document.getElementById("mapa-png").addEventListener("click", () => {
    if (!cy) return;
    const a = document.createElement("a");
    a.href = cy.png({ full: true, scale: 2, bg: "#ffffff" });
    a.download = "mapa_tematico.png";
    a.click();
  });
  carregar().catch((e) => aviso(e.message, "erro"));
})();
