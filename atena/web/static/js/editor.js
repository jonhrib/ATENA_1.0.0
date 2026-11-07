/* Editor de transcrições: seleção de fragmentos, codificação, notas, relevância e navegação. */
(function () {
  "use strict";
  const { api, aviso, esc, barraSelecao, ligarBusca } = window.ATENA;
  const T = document.getElementById("transcricao");
  if (!T) return;
  const DOC = +T.dataset.doc;
  const MODO = T.dataset.modo;
  const URL_FRAG = T.dataset.urlFrag;
  const URL_FRAG_BASE = T.dataset.urlFragBase; // termina em "/fragmentos/"
  const painel = document.getElementById("painel-fragmento");
  const dlgCod = document.getElementById("dlg-codificar");
  const dlgTxt = document.getElementById("dlg-texto");
  let codigos = [];

  async function carregarCodigos() {
    codigos = await api(T.dataset.urlCodigos);
  }

  function pedirTexto(titulo, ajuda, valor = "") {
    return new Promise((ok) => {
      document.getElementById("txt-titulo").textContent = titulo;
      document.getElementById("txt-ajuda").textContent = ajuda || "";
      const ta = document.getElementById("txt-valor");
      ta.value = valor;
      dlgTxt.returnValue = "";
      dlgTxt.showModal();
      dlgTxt.onclose = () => ok(dlgTxt.returnValue === "ok" ? ta.value : null);
    });
  }

  function listaCodigos(excluir = []) {
    const box = document.getElementById("cod-lista");
    box.innerHTML = codigos.filter((c) => !excluir.includes(c.id)).map((c) =>
      `<label data-texto="${esc(c.nome)}"><input type="checkbox" value="${c.id}"><span class="chip" style="margin:0"><i style="background:${esc(c.cor)}"></i>${esc(c.nome)}</span><small class="suave">${c.n} frag.</small></label>`).join("")
      || '<p class="suave" style="padding:8px">Nenhum código ainda — crie o primeiro abaixo.</p>';
    const filtro = document.getElementById("cod-filtro");
    filtro.value = "";
    filtro.oninput = () => box.querySelectorAll("label").forEach((l) => l.classList.toggle("oculto", !l.dataset.texto.toLowerCase().includes(filtro.value.toLowerCase())));
  }

  function abrirCodificacao(trecho, excluir = []) {
    return new Promise(async (ok) => {
      await carregarCodigos();
      document.getElementById("cod-trecho").textContent = trecho;
      ["cod-novo-nome", "cod-novo-desc", "cod-just"].forEach((id) => (document.getElementById(id).value = ""));
      listaCodigos(excluir);
      dlgCod.returnValue = "";
      dlgCod.showModal();
      setTimeout(() => document.getElementById("cod-filtro").focus(), 50);
      dlgCod.onclose = () => {
        if (dlgCod.returnValue !== "ok") return ok(null);
        const ids = Array.from(document.querySelectorAll("#cod-lista input:checked")).map((i) => +i.value);
        const nome = document.getElementById("cod-novo-nome").value.trim();
        if (!ids.length && !nome) { aviso("Escolha um código existente ou informe o nome de um novo código.", "erro"); return ok(null); }
        ok({ codigos: ids, novo_codigo: nome ? { nome, descricao: document.getElementById("cod-novo-desc").value.trim() } : null,
          justificativa: document.getElementById("cod-just").value.trim() });
      };
    });
  }

  const irPara = (fid) => { location.hash = "frag-" + fid; location.reload(); };

  async function codificar(sel) {
    const d = await abrirCodificacao(sel.texto);
    if (!d) return;
    try {
      const r = await api(URL_FRAG, { documento_id: DOC, inicio: sel.inicio, fim: sel.fim, texto: sel.texto, ...d });
      irPara(r.fragmento.id);
    } catch (e) { aviso(e.message, "erro"); }
  }

  async function relevante(sel, comNota) {
    let nota = "";
    if (comNota) {
      nota = await pedirTexto("Nota sobre o trecho", "A nota fica no diário e vinculada a este trecho.");
      if (nota === null || !nota.trim()) return;
    }
    try {
      const r = await api(URL_FRAG, { documento_id: DOC, inicio: sel.inicio, fim: sel.fim, texto: sel.texto, so_relevante: true, nota });
      if (comNota) await api(URL_FRAG_BASE + r.fragmento.id + "/nota", { texto: nota, tipo: MODO === "leitura" ? "familiarizacao" : "memo", documento_id: DOC });
      irPara(r.fragmento.id);
    } catch (e) { aviso(e.message, "erro"); }
  }

  async function sugerirIA(sel) {
    try {
      const r = await api(T.dataset.urlIa, { operacao: "sugerir_codigos", params: { passagens: [{ documento_id: DOC, inicio: sel.inicio, fim: sel.fim }] } });
      location.href = r.url;
    } catch (e) { aviso(e.message, "erro"); }
  }

  const botoes = MODO === "leitura"
    ? [{ rotulo: "Marcar como relevante", acao: (s) => relevante(s, false) }, { rotulo: "Anotar", acao: (s) => relevante(s, true) }]
    : [{ rotulo: "Codificar", acao: codificar }, { rotulo: "Relevante", acao: (s) => relevante(s, false) }, { rotulo: "Anotar", acao: (s) => relevante(s, true) }];
  if (MODO !== "leitura" && T.dataset.ia === "1") botoes.push({ rotulo: "✦ Sugerir códigos (IA)", acao: sugerirIA });
  barraSelecao(T, botoes);
  ligarBusca(T);

  // ---------------------------------------------------------------- painel do fragmento
  function focar(fid) {
    T.querySelectorAll(".seg.focado").forEach((s) => s.classList.remove("focado"));
    const segs = Array.from(T.querySelectorAll(".seg[data-frags]")).filter((s) => s.dataset.frags.split(",").includes(String(fid)));
    segs.forEach((s) => s.classList.add("focado"));
    if (segs[0]) window.ATENA.rolarPara(segs[0]);
  }

  async function mostrarFragmento(fid) {
    let f;
    try { f = await api(URL_FRAG_BASE + fid); } catch (e) { return aviso(e.message, "erro"); }
    focar(fid);
    const sit = f.situacao !== "ok" ? `<div class="aviso ${f.situacao === "inconsistente" ? "erro" : "alerta"}"><div>
      ${f.situacao === "inconsistente" ? "O texto deste fragmento foi alterado numa edição da transcrição." : "Este fragmento foi realocado automaticamente após uma edição."}
      <br>Texto original do fragmento: <i>${esc(f.texto)}</i><div class="linha" style="margin-top:6px">
      <button class="btn pq" data-res="confirmar">Aceitar trecho atual</button><button class="btn sec pq" data-res="reposicionar">Usar a seleção atual</button></div></div></div>` : "";
    const cods = f.codigos.map((c) => `<span class="chip"><i style="background:${esc(c.cor)}"></i><a href="${T.dataset.urlCodigoBase}${c.id}">${esc(c.nome)}</a>
      ${c.origem === "ia" ? '<span class="etiqueta roxo">IA</span>' : ""}<button title="Retirar este código do fragmento" data-remover="${c.codificacao_id}">×</button></span>`).join("") || '<span class="suave">Sem códigos.</span>';
    const temas = f.temas.map((t) => `<span class="etiqueta petroleo">${esc(t.nome)}</span>`).join(" ");
    const notas = (f.notas || []).map((n) => `<div class="fragmento" style="margin:6px 0"><div class="meta">${esc(n.criado_em.slice(0, 16).replace("T", " "))}</div>${esc(n.texto)}</div>`).join("");
    painel.innerHTML = `<h3>Fragmento nº ${f.id}</h3>${sit}
      <div class="citacao-bloco" style="max-height:220px;overflow:auto">${esc(f.texto)}<footer>${esc(f.participante_codigo || "—")} · ${esc(f.documento_codigo)} · posições ${f.inicio}–${f.fim}</footer></div>
      <div style="margin:8px 0">${cods}</div>${temas ? '<div class="suave" style="font-size:.82rem">Temas: ' + temas + "</div>" : ""}
      ${f.nota ? '<p class="suave" style="font-size:.85rem">Nota: ' + esc(f.nota) + "</p>" : ""}${notas}
      <div class="linha" style="margin-top:8px"><button class="btn pq" data-acao="add">+ Código</button>
      <button class="btn sec pq" data-acao="rel">${f.relevante ? "Desmarcar relevante" : "Marcar relevante"}</button>
      <button class="btn sec pq" data-acao="nota">Anotar</button>
      ${T.dataset.ia === "1" ? '<button class="btn roxo pq" data-acao="ia">✦ IA</button>' : ""}
      <button class="btn perigo pq" data-acao="desativar">Remover</button></div>`;
    painel.onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      try {
        if (b.dataset.remover) {
          const j = await pedirTexto("Retirar código do fragmento", "Justificativa (registrada no histórico):");
          if (j === null) return;
          await api(URL_FRAG_BASE + fid + "/remover_codificacao", { codificacao_id: +b.dataset.remover, justificativa: j });
          return irPara(fid);
        }
        if (b.dataset.res) {
          let extra = {};
          if (b.dataset.res === "reposicionar") {
            const s = window.ATENA.selecaoEm(T);
            if (!s) return aviso("Selecione antes, no texto, o trecho correto para este fragmento.", "info");
            extra = { inicio: s.inicio, fim: s.fim };
          }
          const j = await pedirTexto("Revisar associação", "Observação sobre a revisão (opcional):");
          if (j === null) return;
          await api(URL_FRAG_BASE + fid + "/resolver", { acao: b.dataset.res, justificativa: j, ...extra });
          return irPara(fid);
        }
        const a = b.dataset.acao;
        if (a === "add") {
          const d = await abrirCodificacao(f.texto, f.codigos.map((c) => c.id));
          if (!d) return;
          await api(URL_FRAG_BASE + fid + "/codigo", d);
          return irPara(fid);
        }
        if (a === "rel") { await api(URL_FRAG_BASE + fid + "/editar", { relevante: !f.relevante }); return irPara(fid); }
        if (a === "nota") {
          const t = await pedirTexto("Nota sobre o fragmento", "");
          if (!t || !t.trim()) return;
          await api(URL_FRAG_BASE + fid + "/nota", { texto: t, tipo: MODO === "leitura" ? "familiarizacao" : "memo", documento_id: DOC });
          return mostrarFragmento(fid);
        }
        if (a === "ia") {
          const r = await api(T.dataset.urlIa, { operacao: "sugerir_codigos", params: { fragmentos: [fid] } });
          location.href = r.url;
        }
        if (a === "desativar") {
          const j = await pedirTexto("Remover fragmento", "O fragmento e suas associações serão desativados (o histórico é preservado). Justificativa:");
          if (!j || !j.trim()) return;
          await api(URL_FRAG_BASE + fid + "/desativar", { justificativa: j });
          location.hash = ""; location.reload();
        }
      } catch (err) { aviso(err.message, "erro"); }
    };
  }

  T.addEventListener("click", (e) => {
    if (!window.getSelection().isCollapsed) return;
    const seg = e.target.closest(".seg[data-frags]");
    if (!seg) return;
    const ids = seg.dataset.frags.split(",").filter(Boolean).map(Number);
    if (!ids.length) return;
    // em trechos sobrepostos, alterna entre os fragmentos a cada clique
    const atual = +(seg.dataset.ultimo || 0);
    const prox = ids[(ids.indexOf(atual) + 1) % ids.length];
    seg.dataset.ultimo = prox;
    mostrarFragmento(prox);
  });

  document.querySelectorAll(".destacar-codigo").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const dados = JSON.parse(document.getElementById("dados-frags").textContent);
    const cid = +a.dataset.codigo;
    const fids = Object.keys(dados).filter((k) => dados[k].c.includes(cid));
    T.querySelectorAll(".seg.focado").forEach((s) => s.classList.remove("focado"));
    T.querySelectorAll(".seg[data-frags]").forEach((s) => { if (s.dataset.frags.split(",").some((x) => fids.includes(x))) s.classList.add("focado"); });
    const primeiro = T.querySelector(".seg.focado");
    if (primeiro) window.ATENA.rolarPara(primeiro);
  }));

  const m = location.hash.match(/^#frag-(\d+)$/);
  if (m) setTimeout(() => mostrarFragmento(+m[1]), 80);
})();
