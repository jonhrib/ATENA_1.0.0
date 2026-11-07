"""Fase 6 — produção de relatórios com evidências rastreáveis.

Os relatórios são escritos em Markdown simples. Citações de dados **não são
digitadas**: são inseridas como marcações ``[[frag:N]]``, que o ATENA
substitui pelo texto exato do fragmento N, acompanhado do identificador
anônimo do participante e da entrevista. Assim, cada citação do relatório
permanece vinculada ao fragmento original. A verificação aponta citações
digitadas manualmente que não existem nos dados.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from ..textos import dobrar
from .projetos import ErroValidacao, Projeto

TIPOS = {"final": "Relatório final da análise", "metodologico": "Relatório metodológico",
         "sinteses": "Sínteses dos temas (rascunhos)", "livre": "Texto livre"}

TOKEN = re.compile(r"\[\[frag:(\d+)\]\]")
MARCA_IA = "[TEXTO PRELIMINAR GERADO POR IA — REVISAR]"


# ------------------------------------------------------------------ CRUD
def criar(p: Projeto, tipo: str, titulo: str, conteudo: str = "", origem: str = "pesquisador") -> int:
    if tipo not in TIPOS:
        raise ErroValidacao("Tipo de relatório inválido.")
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO relatorios(tipo, titulo, criado_em, atualizado_em) VALUES (?,?,?,?)",
                            (tipo, titulo.strip() or TIPOS[tipo], momento, momento))
        rid = cur.lastrowid
        p.con.execute("INSERT INTO relatorio_versoes(relatorio_id, numero, conteudo, origem, observacao, criado_em)"
                      " VALUES (?,?,?,?,?,?)", (rid, 1, conteudo, origem, "versão inicial", momento))
        registrar(p.con, "relatorio", rid, "criar", fase=6, depois={"tipo": tipo, "titulo": titulo, "caracteres": len(conteudo)},
                  origem="ia" if origem == "ia" else "pesquisador")
        p.con.execute("UPDATE fases SET situacao = 'em_andamento', iniciada_em = COALESCE(iniciada_em, ?)"
                      " WHERE numero = 6 AND situacao = 'nao_iniciada'", (momento,))
    return rid


def obter(p: Projeto, rid: int, numero: int | None = None) -> dict:
    r = linha(p.con.execute("SELECT * FROM relatorios WHERE id = ?", (rid,)).fetchone())
    if not r:
        raise ErroValidacao("Relatório não encontrado.")
    if numero:
        v = p.con.execute("SELECT * FROM relatorio_versoes WHERE relatorio_id = ? AND numero = ?", (rid, numero)).fetchone()
    else:
        v = p.con.execute("SELECT * FROM relatorio_versoes WHERE relatorio_id = ? ORDER BY numero DESC LIMIT 1", (rid,)).fetchone()
    r["versao"] = linha(v)
    r["tipo_rotulo"] = TIPOS.get(r["tipo"], r["tipo"])
    r["versoes"] = linhas(p.con.execute("SELECT id, numero, origem, observacao, aprovado, criado_em, LENGTH(conteudo) AS caracteres"
                                        " FROM relatorio_versoes WHERE relatorio_id = ? ORDER BY numero DESC", (rid,)))
    return r


def listar(p: Projeto) -> list[dict]:
    out = linhas(p.con.execute("SELECT * FROM relatorios WHERE ativo = 1 ORDER BY id DESC"))
    for r in out:
        r["tipo_rotulo"] = TIPOS.get(r["tipo"], r["tipo"])
        r["n_versoes"] = p.con.execute("SELECT COUNT(*) FROM relatorio_versoes WHERE relatorio_id = ?", (r["id"],)).fetchone()[0]
    return out


def salvar_versao(p: Projeto, rid: int, conteudo: str, observacao: str = "", origem: str = "pesquisador",
                  versao_base: int | None = None) -> int:
    r = obter(p, rid)
    if versao_base is not None and int(versao_base) != r["versao"]["numero"]:
        raise ErroValidacao(f"O relatório foi alterado em outra janela (versão atual nº {r['versao']['numero']}). "
                            "Recarregue antes de salvar.")
    conteudo = (conteudo or "").replace("\r\n", "\n")
    if conteudo == r["versao"]["conteudo"]:
        raise ErroValidacao("Nenhuma alteração para salvar.")
    n = r["versao"]["numero"] + 1
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO relatorio_versoes(relatorio_id, numero, conteudo, origem, observacao, criado_em)"
                            " VALUES (?,?,?,?,?,?)", (rid, n, conteudo, origem, observacao, agora()))
        estava = r["situacao"]
        p.con.execute("UPDATE relatorios SET situacao = 'rascunho', atualizado_em = ? WHERE id = ?", (agora(), rid))
        registrar(p.con, "relatorio", rid, "nova_versao", fase=6,
                  antes={"versao": n - 1, "caracteres": len(r["versao"]["conteudo"]), "situacao": estava},
                  depois={"versao": n, "caracteres": len(conteudo), "situacao": "rascunho",
                          "citacoes": len(TOKEN.findall(conteudo))},
                  justificativa=observacao, origem="ia" if origem == "ia" else "pesquisador")
        return cur.lastrowid


def restaurar_versao(p: Projeto, rid: int, numero: int, justificativa: str) -> int:
    v = obter(p, rid, numero)["versao"]
    if not v:
        raise ErroValidacao("Versão não encontrada.")
    return salvar_versao(p, rid, v["conteudo"], f"Restauração da versão nº {numero}: {justificativa}")


def aprovar(p: Projeto, rid: int, observacao: str) -> None:
    r = obter(p, rid)
    if MARCA_IA in r["versao"]["conteudo"]:
        raise ErroValidacao("O texto ainda contém trechos marcados como preliminares de IA. Revise-os e remova a marcação "
                            f"“{MARCA_IA}” antes de aprovar.")
    problemas = verificar(p, r["versao"]["conteudo"])
    graves = [x for x in problemas if x["gravidade"] == "erro"]
    if graves:
        raise ErroValidacao("Corrija antes de aprovar: " + "; ".join(x["texto"] for x in graves[:5]))
    if not observacao.strip():
        raise ErroValidacao("Registre uma observação sobre a aprovação.")
    with transacao(p.con):
        p.con.execute("UPDATE relatorio_versoes SET aprovado = 1 WHERE id = ?", (r["versao"]["id"],))
        p.con.execute("UPDATE relatorios SET situacao = 'aprovado', atualizado_em = ? WHERE id = ?", (agora(), rid))
        registrar(p.con, "relatorio", rid, "aprovar", fase=6, depois={"versao": r["versao"]["numero"],
                                                                      "alertas": len(problemas)}, justificativa=observacao)


def arquivar(p: Projeto, rid: int, justificativa: str) -> None:
    with transacao(p.con):
        p.con.execute("UPDATE relatorios SET ativo = 0, atualizado_em = ? WHERE id = ?", (agora(), rid))
        registrar(p.con, "relatorio", rid, "arquivar", fase=6, justificativa=justificativa)


def anexar_texto(p: Projeto, tema_id: int, texto: str, origem: str = "ia") -> int:
    t = p.con.execute("SELECT nome FROM temas WHERE id = ?", (tema_id,)).fetchone()
    r = p.con.execute("SELECT id FROM relatorios WHERE tipo = 'sinteses' AND ativo = 1 ORDER BY id LIMIT 1").fetchone()
    bloco = f"\n\n## {t['nome'] if t else 'Tema'}\n\n{MARCA_IA if origem == 'ia' else ''}\n\n{texto.strip()}\n"
    if not r:
        return criar(p, "sinteses", "Sínteses dos temas (rascunhos)", "# Sínteses dos temas" + bloco, origem=origem)
    atual = obter(p, r[0])["versao"]["conteudo"]
    salvar_versao(p, r[0], atual + bloco, "Síntese de IA aprovada pelo pesquisador e anexada como rascunho", origem=origem)
    return r[0]


# ------------------------------------------------------------------ fragmentos e verificação
def _frag(p, fid):
    return linha(p.con.execute(
        "SELECT f.*, d.codigo AS documento_codigo, pa.codigo AS participante_codigo FROM fragmentos f"
        " JOIN documentos d ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id"
        " WHERE f.id = ?", (fid,)).fetchone())


def verificar(p: Projeto, conteudo: str) -> list[dict]:
    out = []
    for fid in sorted({int(x) for x in TOKEN.findall(conteudo)}):
        f = _frag(p, fid)
        if not f:
            out.append({"gravidade": "erro", "texto": f"Citação [[frag:{fid}]] aponta para um fragmento inexistente."})
        elif not f["ativo"]:
            out.append({"gravidade": "erro", "texto": f"Citação [[frag:{fid}]] aponta para um fragmento removido."})
        elif f["situacao"] == "inconsistente":
            out.append({"gravidade": "erro", "texto": f"O fragmento {fid} está inconsistente após edição da transcrição; revise-o."})
        elif f["situacao"] == "realocado":
            out.append({"gravidade": "aviso", "texto": f"O fragmento {fid} foi realocado após edição; confira a associação."})
    textos = [r[0] for r in p.con.execute(
        "SELECT t.texto FROM transcricao_versoes t WHERE t.numero = (SELECT MAX(numero) FROM transcricao_versoes x"
        " WHERE x.documento_id = t.documento_id)")]
    corpus = dobrar(" ".join(" ".join(t.split()) for t in textos))
    sem_tokens = TOKEN.sub("", conteudo)
    for trecho in re.findall(r"[“\"]([^”\"\n]{25,})[”\"]", sem_tokens):
        if dobrar(" ".join(trecho.split())) not in corpus:
            out.append({"gravidade": "aviso", "texto": f"Trecho entre aspas não encontrado nas transcrições: “{trecho[:90]}…”. "
                                                         "Se for uma fala, insira-a como citação vinculada [[frag:N]]."})
    if p.cofre_aberto():
        from . import pii
        for c in pii.detectar(conteudo, protegidos=pii.protegidos_do_projeto(p),
                              identidades=pii.identidades_do_projeto(p), usar_spacy=False):
            if c.fonte in ("mapeamento",) or (c.fonte == "padrao" and c.confianca == "alta"):
                out.append({"gravidade": "erro", "texto": f"Possível dado identificável no texto: “{c.trecho}” ({c.categoria})."})
    else:
        out.append({"gravidade": "info", "texto": "Cofre bloqueado: o texto não foi comparado com o mapeamento de identidades."})
    if MARCA_IA in conteudo:
        out.append({"gravidade": "aviso", "texto": "Há trechos preliminares de IA ainda não revisados."})
    return out


# ------------------------------------------------------------------ rascunhos automáticos
def _q(p, sql, *a):
    return p.con.execute(sql, a).fetchone()[0]


def rascunho_metodologico(p: Projeto) -> str:
    from . import fases, notas
    pr = p.dados()
    docs = _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1")
    anon = _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND situacao = 'anonimizado'")
    parts = _q(p, "SELECT COUNT(*) FROM participantes WHERE ativo = 1")
    l = [f"# Relatório metodológico — {pr['nome']}", "",
         "## Problema, pergunta e objetivo", "",
         f"**Problema de pesquisa.** {pr['problema'] or '[a preencher]'}", "",
         f"**Pergunta de pesquisa.** {pr['pergunta'] or '[a preencher]'}", "",
         f"**Objetivo.** {pr['objetivo'] or '[a preencher]'}", "",
         "## Abordagem analítica", "",
         f"{pr['metodologia']}. A análise seguiu as seis fases propostas por Braun e Clarke (2006), de forma recursiva, "
         "com registro das decisões analíticas no ATENA.", ""]
    if pr["referencias"]:
        l += ["**Referências metodológicas.** " + pr["referencias"], ""]
    l += ["## Corpus e preparação dos dados", "",
          f"O corpus é composto por {docs} documento(s), dos quais {anon} anonimizado(s), associados a {parts} participante(s) "
          "identificados exclusivamente por códigos (PARTICIPANTE_001, PARTICIPANTE_002, …).", ""]
    cats = Counter()
    for h in p.con.execute("SELECT depois FROM historico WHERE entidade = 'anonimizacao' AND operacao = 'aplicar'"):
        d = json.loads(h[0] or "{}")
        cats.update(d.get("por_categoria", {}))
    l += ["Os documentos originais foram preservados em área protegida e cifrada. A pseudonimização combinou detecção "
          "automática local (padrões, listas e heurísticas) com conferência humana de cada substituição; o mapeamento entre "
          "códigos e identidades foi mantido cifrado e separado dos dados de análise. Termos protegidos (como nomes de "
          "instituições relevantes para a pesquisa) foram preservados por decisão metodológica.", ""]
    if cats:
        l += ["| Categoria substituída | Ocorrências |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(cats.items())] + [""]
    l += ["## Desenvolvimento das fases", ""]
    for f in fases.listar(p):
        l += [f"### Fase {f['numero']} — {f['nome']}", "",
              f"Situação: {f['situacao_rotulo']}" + (f" (concluída em {f['concluida_em'][:10]})" if f["concluida_em"] else "") + ".", ""]
        if f["observacao"]:
            l += [f"Registro do pesquisador: {f['observacao']}", ""]
        l += ["Indicadores descritivos: " + "; ".join(f"{a}: {b}" for a, b in f["indicadores"]) + ".", ""]
    dec = notas.decisoes(p)
    if dec:
        l += ["## Decisões metodológicas registradas", ""]
        for d in reversed(dec):
            l += [f"- **{d['titulo']}** ({d['criado_em'][:10]}): {d['descricao']} Justificativa: {d['justificativa']}"]
        l += [""]
    n_ia = _q(p, "SELECT COUNT(*) FROM ia_interacoes WHERE situacao = 'respondida'")
    l += ["## Uso de inteligência artificial", ""]
    if n_ia:
        sug = Counter(r[0] for r in p.con.execute("SELECT situacao FROM sugestoes"))
        ops = Counter(r[0] for r in p.con.execute("SELECT operacao FROM ia_interacoes WHERE situacao = 'respondida'"))
        modelos = sorted({r[0] for r in p.con.execute("SELECT modelo FROM ia_interacoes WHERE situacao = 'respondida'")})
        l += [f"Foram realizadas {n_ia} consulta(s) a modelo(s) de linguagem ({', '.join(modelos)}), sempre com conteúdo "
              "anonimizado, prévia e autorização explícita do pesquisador. As respostas foram tratadas como sugestões: "
              f"{sug.get('aceita', 0)} aceita(s), {sug.get('editada', 0)} aceita(s) com edição, {sug.get('rejeitada', 0)} "
              f"rejeitada(s) e {sug.get('pendente', 0)} pendente(s).", "",
              "| Operação | Consultas |", "|---|---|"] + [f"| {k} | {v} |" for k, v in ops.items()] + [""]
    else:
        l += ["Não foram utilizados modelos de linguagem na análise.", ""]
    l += ["## Limitações", "", "[Pesquisador: descreva as limitações do estudo e da análise.]", ""]
    return "\n".join(l)


def rascunho_final(p: Projeto) -> str:
    from . import temas as tm
    pr = p.dados()
    arv = tm.arvore(p)
    l = [f"# Resultados — {pr['nome']}", "",
         f"Esta seção apresenta os temas construídos na análise temática indutiva. Pergunta de pesquisa: "
         f"{pr['pergunta'] or '[a preencher]'}", "",
         "## Quadro de temas, subtemas e códigos", "", "| Tema | Subtemas | Códigos | Situação |", "|---|---|---|---|"]
    for t in arv:
        cods = sorted({c["nome"] for c in t["codigos"]} | {c["nome"] for s in t.get("subtemas", []) for c in s["codigos"]})
        l.append(f"| {t['nome']} | {', '.join(s['nome'] for s in t.get('subtemas', [])) or '—'} | {', '.join(cods) or '—'} "
                 f"| {t['situacao_rotulo']} |")
    l.append("")
    for t in arv:
        d = tm.obter(p, t["id"])
        l += [f"## {d['nome']}", ""]
        if d["situacao"] != "definido":
            l += [f"> Atenção: tema ainda não definido ({d['situacao_rotulo']}).", ""]
        if d["conceito_central"]:
            l += [f"**Conceito central.** {d['conceito_central']}", ""]
        if d["descricao"]:
            l += [d["descricao"], ""]
        if d["escopo"]:
            l += [f"**Escopo.** {d['escopo']}", ""]
        if d["limites"]:
            l += [f"**Limites.** {d['limites']}", ""]
        if d["relacao_pergunta"]:
            l += [f"**Relação com a pergunta de pesquisa.** {d['relacao_pergunta']}", ""]
        for s in d["subtemas"]:
            ds = tm.obter(p, s["id"])
            l += [f"### Subtema: {ds['nome']}", "", ds["descricao"] or "[descrição do subtema]", ""]
            for e in ds["evidencias_representativas"]:
                l += [f"[[frag:{e['id']}]]", ""]
        l += ["[Pesquisador: desenvolva a interpretação analítica do tema, articulando as evidências abaixo.]", ""]
        for e in d["evidencias_representativas"]:
            l += [f"[[frag:{e['id']}]]", ""]
        if not d["evidencias_representativas"]:
            ev = tm.evidencias(p, d["id"])[:5]
            if ev:
                l += ["> Evidências associadas ao tema (ainda não selecionadas como representativas — escolha as mais ilustrativas):", ""]
                for e in ev:
                    l += [f"[[frag:{e['id']}]]", ""]
    l += ["## Distribuição dos temas entre as entrevistas", "",
          "As contagens abaixo são descritivas e não indicam, por si, a relevância de um tema.", ""]
    docs = linhas(p.con.execute("SELECT d.id, d.codigo, pa.codigo AS part FROM documentos d LEFT JOIN participantes pa"
                                " ON pa.id = d.participante_id WHERE d.ativo = 1 AND d.situacao = 'anonimizado' ORDER BY d.codigo"))
    if docs and arv:
        l.append("| Tema | " + " | ".join(d["part"] or d["codigo"] for d in docs) + " |")
        l.append("|---|" + "---|" * len(docs))
        for t in arv:
            cont = Counter(f["documento_id"] for f in tm.evidencias(p, t["id"]))
            l.append(f"| {t['nome']} | " + " | ".join(str(cont.get(d["id"], 0)) for d in docs) + " |")
        l.append("")
    return "\n".join(l)


def gerar_rascunho(p: Projeto, tipo: str) -> int:
    if tipo == "metodologico":
        return criar(p, "metodologico", "Relatório metodológico", rascunho_metodologico(p), origem="pesquisador")
    if tipo == "final":
        return criar(p, "final", "Relatório final da análise", rascunho_final(p), origem="pesquisador")
    return criar(p, "livre", "Novo texto", "# Título\n\n")


# ------------------------------------------------------------------ renderização
def _inline_html(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", t)
    return t


def blocos(p: Projeto, conteudo: str) -> list[dict]:
    """Interpreta o Markdown simples do ATENA em blocos (títulos, parágrafos, listas, tabelas, citações)."""
    out = []
    linhas_ = conteudo.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(linhas_):
        ln = linhas_[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            out.append({"tipo": "titulo", "nivel": len(m[1]), "texto": m[2]}); i += 1; continue
        if re.fullmatch(r"(\[\[frag:\d+\]\]\s*)+", s):
            for fid in TOKEN.findall(s):
                f = _frag(p, int(fid))
                out.append({"tipo": "citacao", "fid": int(fid), "texto": f["texto"] if f else None,
                            "participante": (f["participante_codigo"] if f else None),
                            "documento": f["documento_codigo"] if f else None,
                            "valida": bool(f and f["ativo"] and f["situacao"] != "inconsistente")})
            i += 1; continue
        if s.startswith("|"):
            rows = []
            while i < len(linhas_) and linhas_[i].strip().startswith("|"):
                cel = [c.strip() for c in linhas_[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cel if c):
                    rows.append(cel)
                i += 1
            out.append({"tipo": "tabela", "linhas": rows}); continue
        if re.match(r"^[-*]\s+", s):
            itens = []
            while i < len(linhas_) and re.match(r"^\s*[-*]\s+", linhas_[i]):
                itens.append(re.sub(r"^\s*[-*]\s+", "", linhas_[i]).strip()); i += 1
            out.append({"tipo": "lista", "itens": itens}); continue
        if re.match(r"^\d+[.)]\s+", s):
            itens = []
            while i < len(linhas_) and re.match(r"^\s*\d+[.)]\s+", linhas_[i]):
                itens.append(re.sub(r"^\s*\d+[.)]\s+", "", linhas_[i]).strip()); i += 1
            out.append({"tipo": "numerada", "itens": itens}); continue
        if s.startswith(">"):
            txt = []
            while i < len(linhas_) and linhas_[i].strip().startswith(">"):
                txt.append(linhas_[i].strip()[1:].strip()); i += 1
            out.append({"tipo": "destaque", "texto": " ".join(txt)}); continue
        if s in ("---", "***"):
            out.append({"tipo": "separador"}); i += 1; continue
        par = []
        while i < len(linhas_) and linhas_[i].strip() and not re.match(r"^(#{1,4}\s|\||[-*]\s|\d+[.)]\s|>)", linhas_[i].strip()) \
                and not re.fullmatch(r"(\[\[frag:\d+\]\]\s*)+", linhas_[i].strip()):
            par.append(linhas_[i].strip()); i += 1
        out.append({"tipo": "paragrafo", "texto": " ".join(par)})
    return out


def _inline_token(p, t: str) -> str:
    def rep(m):
        f = _frag(p, int(m[1]))
        if not f:
            return f"[fragmento {m[1]} inexistente]"
        return f"“{' '.join(f['texto'].split())}” ({f['participante_codigo'] or f['documento_codigo']})"
    return TOKEN.sub(rep, t)


def html_relatorio(p: Projeto, conteudo: str) -> str:
    partes = []
    for b in blocos(p, conteudo):
        t = b["tipo"]
        if t == "titulo":
            partes.append(f"<h{b['nivel'] + 1}>{_inline_html(_inline_token(p, b['texto']))}</h{b['nivel'] + 1}>")
        elif t == "paragrafo":
            cls = ' class="marca-ia"' if MARCA_IA in b["texto"] else ""
            partes.append(f"<p{cls}>{_inline_html(_inline_token(p, b['texto']))}</p>")
        elif t == "destaque":
            partes.append(f"<div class='destaque'>{_inline_html(_inline_token(p, b['texto']))}</div>")
        elif t in ("lista", "numerada"):
            tag = "ul" if t == "lista" else "ol"
            partes.append(f"<{tag}>" + "".join(f"<li>{_inline_html(_inline_token(p, x))}</li>" for x in b["itens"]) + f"</{tag}>")
        elif t == "tabela":
            if b["linhas"]:
                cab, *corpo = b["linhas"]
                partes.append("<table class='tabela'><thead><tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in cab) +
                              "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>"
                                                               for r in corpo) + "</tbody></table>")
        elif t == "citacao":
            if b["texto"] is None:
                partes.append(f"<blockquote class='citacao invalida'>Fragmento {b['fid']} inexistente.</blockquote>")
            else:
                aviso = "" if b["valida"] else " <span class='etiqueta alerta'>revisar fragmento</span>"
                partes.append(f"<blockquote class='citacao' data-frag='{b['fid']}'>“{html.escape(' '.join(b['texto'].split()))}”"
                              f"<footer>{html.escape(b['participante'] or '—')} · {html.escape(b['documento'])} · fragmento nº {b['fid']}"
                              f"{aviso}</footer></blockquote>")
        elif t == "separador":
            partes.append("<hr>")
    return "\n".join(partes)


def texto_puro(p: Projeto, conteudo: str) -> str:
    """Markdown com as citações expandidas (para exportação .md)."""
    def rep_linha(m):
        f = _frag(p, int(m[1]))
        if not f:
            return f"> [fragmento {m[1]} inexistente]"
        return f"> “{' '.join(f['texto'].split())}”\n> — {f['participante_codigo'] or '—'}, {f['documento_codigo']}, fragmento nº {f['id']}"
    linhas_ = []
    for ln in conteudo.split("\n"):
        if re.fullmatch(r"\s*(\[\[frag:\d+\]\]\s*)+", ln):
            linhas_.append("\n\n".join(rep_linha(m) for m in TOKEN.finditer(ln)))
        else:
            linhas_.append(_inline_token(p, ln))
    return "\n".join(linhas_)
