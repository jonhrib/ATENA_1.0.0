"""Dados para as visualizações. Todas as contagens são descritivas e rastreáveis aos fragmentos."""
from __future__ import annotations

import json
import re
from collections import Counter
from itertools import combinations

from .. import config
from ..db import linhas
from ..textos import dobrar
from . import temas as tm
from .projetos import Projeto


def _filtros_sql(f: dict, alias_f="f", alias_d="d"):
    cond, params = [], []
    if f.get("documento"):
        cond.append(f"{alias_f}.documento_id = ?"); params.append(int(f["documento"]))
    if f.get("participante"):
        cond.append(f"{alias_d}.participante_id = ?"); params.append(int(f["participante"]))
    return cond, params


def mapa_tematico(p: Projeto, incluir_coocorrencia: bool = True) -> dict:
    """Nós (temas, subtemas, códigos) e arestas para o mapa interativo (Cytoscape)."""
    nos, arestas = [], []
    arv = tm.arvore(p)
    stats = tm.estatisticas(p)
    from .codificacao import listar_codigos
    cods = {c["id"]: c for c in listar_codigos(p)}
    colocados = {}
    for t in arv:
        nos.append({"data": {"id": f"t{t['id']}", "label": t["nome"], "tipo": "tema", "tema_id": t["id"],
                             "situacao": t["situacao"], "cor": t["cor"] or "#0f766e", **stats.get(t["id"], {})}})
        for c in t["codigos"]:
            colocados.setdefault(c["id"], []).append(f"t{t['id']}")
        for s in t.get("subtemas", []):
            nos.append({"data": {"id": f"t{s['id']}", "label": s["nome"], "tipo": "subtema", "tema_id": s["id"],
                                 "parent": f"t{t['id']}", "situacao": s["situacao"], "cor": s["cor"] or "#6d28d9",
                                 **stats.get(s["id"], {})}})
            for c in s["codigos"]:
                colocados.setdefault(c["id"], []).append(f"t{s['id']}")
    for cid, c in cods.items():
        pais = colocados.get(cid, [])
        if pais:
            for k, pai in enumerate(pais):
                nid = f"c{cid}" if k == 0 else f"c{cid}_{k}"
                nos.append({"data": {"id": nid, "label": c["nome"], "tipo": "codigo", "codigo_id": cid, "parent": pai,
                                     "n": c["n_fragmentos"], "cor": c["cor"], "duplicado": k > 0, "em_varios": len(pais) > 1}})
        else:
            nos.append({"data": {"id": f"c{cid}", "label": c["nome"], "tipo": "codigo", "codigo_id": cid, "parent": "sem_tema",
                                 "n": c["n_fragmentos"], "cor": c["cor"]}})
    nos.append({"data": {"id": "sem_tema", "label": "Códigos sem tema", "tipo": "area"}})
    if incluir_coocorrencia:
        for (a, b), n in coocorrencia_codigos(p).items():
            arestas.append({"data": {"id": f"e{a}_{b}", "source": f"c{a}", "target": f"c{b}", "peso": n}})
    return {"elements": nos + arestas}


def coocorrencia_codigos(p: Projeto) -> Counter:
    """Pares de códigos aplicados ao mesmo fragmento."""
    por_frag = {}
    for r in p.con.execute("SELECT cf.fragmento_id, cf.codigo_id FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id"
                           " JOIN codigos c ON c.id = cf.codigo_id WHERE cf.ativo = 1 AND f.ativo = 1 AND c.situacao = 'ativo'"):
        por_frag.setdefault(r[0], set()).add(r[1])
    cont = Counter()
    for cs in por_frag.values():
        for a, b in combinations(sorted(cs), 2):
            cont[(a, b)] += 1
    return cont


def frequencia_codigos(p: Projeto, filtros: dict | None = None) -> list[dict]:
    cond, params = _filtros_sql(filtros or {})
    where = (" AND " + " AND ".join(cond)) if cond else ""
    return linhas(p.con.execute(
        "SELECT c.id, c.nome, c.cor, COUNT(DISTINCT f.id) AS fragmentos, COUNT(DISTINCT f.documento_id) AS documentos,"
        " COUNT(DISTINCT d.participante_id) AS participantes FROM codigos c JOIN codificacoes cf ON cf.codigo_id = c.id AND cf.ativo = 1"
        " JOIN fragmentos f ON f.id = cf.fragmento_id AND f.ativo = 1 JOIN documentos d ON d.id = f.documento_id"
        f" WHERE c.situacao = 'ativo'{where} GROUP BY c.id ORDER BY fragmentos DESC, c.nome", params))


def matriz_participantes(p: Projeto, por: str = "codigo", filtros: dict | None = None) -> dict:
    """Matriz de presença (fragmentos) por participante, para códigos ou temas."""
    parts = linhas(p.con.execute("SELECT id, codigo FROM participantes WHERE ativo = 1 ORDER BY numero"))
    sem_part = p.con.execute("SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND participante_id IS NULL").fetchone()[0]
    if sem_part:
        parts.append({"id": 0, "codigo": "(sem participante)"})
    linhas_ = []
    if por == "codigo":
        cods = linhas(p.con.execute("SELECT id, nome, cor FROM codigos WHERE situacao = 'ativo' ORDER BY nome COLLATE NOCASE"))
        cel = {}
        for r in p.con.execute("SELECT cf.codigo_id, COALESCE(d.participante_id, 0), COUNT(DISTINCT f.id) FROM codificacoes cf"
                               " JOIN fragmentos f ON f.id = cf.fragmento_id JOIN documentos d ON d.id = f.documento_id"
                               " WHERE cf.ativo = 1 AND f.ativo = 1 GROUP BY 1, 2"):
            cel[(r[0], r[1])] = r[2]
        for c in cods:
            if filtros and filtros.get("codigo") and int(filtros["codigo"]) != c["id"]:
                continue
            linhas_.append({"id": c["id"], "nome": c["nome"], "tipo": "codigo", "valores": [cel.get((c["id"], pa["id"]), 0) for pa in parts]})
    else:
        for t in tm.listar(p):
            if filtros and filtros.get("tema") and int(filtros["tema"]) != t["id"]:
                continue
            cont = Counter((f["participante_codigo"] or "(sem participante)") for f in tm.evidencias(p, t["id"]))
            linhas_.append({"id": t["id"], "nome": ("↳ " if t["pai_id"] else "") + t["nome"], "tipo": "tema",
                            "valores": [cont.get(pa["codigo"], 0) for pa in parts]})
    maximo = max([v for l in linhas_ for v in l["valores"]] + [1])
    return {"colunas": parts, "linhas": linhas_, "maximo": maximo}


def matriz_temas_documentos(p: Projeto) -> dict:
    docs = linhas(p.con.execute("SELECT d.id, d.codigo, pa.codigo AS participante FROM documentos d LEFT JOIN participantes pa"
                                " ON pa.id = d.participante_id WHERE d.ativo = 1 AND d.situacao = 'anonimizado' ORDER BY d.codigo"))
    linhas_ = []
    for t in tm.listar(p):
        cont = Counter(f["documento_id"] for f in tm.evidencias(p, t["id"]))
        linhas_.append({"id": t["id"], "nome": ("↳ " if t["pai_id"] else "") + t["nome"], "valores": [cont.get(d["id"], 0) for d in docs]})
    return {"colunas": docs, "linhas": linhas_, "maximo": max([v for l in linhas_ for v in l["valores"]] + [1])}


def distribuicao_temas(p: Projeto) -> list[dict]:
    return [{"id": t["id"], "nome": ("↳ " if t["pai_id"] else "") + t["nome"], "fragmentos": t["n_fragmentos"],
             "participantes": t["n_participantes"], "codigos": t["n_codigos"], "situacao": t["situacao_rotulo"]}
            for t in tm.listar(p)]


def comparar_documentos(p: Projeto, a: int, b: int) -> dict:
    def perfil(did):
        return {r[0]: r[1] for r in p.con.execute(
            "SELECT c.nome, COUNT(DISTINCT f.id) FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id"
            " JOIN codigos c ON c.id = cf.codigo_id WHERE f.documento_id = ? AND cf.ativo = 1 AND f.ativo = 1 AND c.situacao = 'ativo'"
            " GROUP BY c.nome", (did,))}
    pa, pb = perfil(a), perfil(b)
    nomes = sorted(set(pa) | set(pb), key=str.lower)
    return {"linhas": [{"codigo": n, "a": pa.get(n, 0), "b": pb.get(n, 0)} for n in nomes],
            "so_a": [n for n in nomes if n in pa and n not in pb], "so_b": [n for n in nomes if n in pb and n not in pa],
            "comuns": [n for n in nomes if n in pa and n in pb]}


def linha_do_tempo(p: Projeto, limite: int = 400) -> list[dict]:
    ops_relevantes = ("criar", "concluir", "reabrir", "confirmar_definicao", "fundir", "dividir", "descartar", "revisar_tema",
                      "registrar", "aplicar", "mesclar", "aprovar", "agrupar_codigo", "criar_subtema", "aceitar",
                      "aceitar_com_edicao", "rejeitar", "restaurar_backup", "excluir", "nova_versao")
    marc = ",".join("?" * len(ops_relevantes))
    out = linhas(p.con.execute(f"SELECT id, momento, entidade, entidade_id, operacao, fase, origem, justificativa, depois"
                               f" FROM historico WHERE operacao IN ({marc}) AND entidade NOT IN ('termo','leitura')"
                               f" ORDER BY id DESC LIMIT ?", list(ops_relevantes) + [limite]))
    for h in out:
        d = json.loads(h["depois"] or "{}")
        h["resumo"] = d.get("nome") or d.get("titulo") or d.get("codigo") or d.get("tema") or ""
    return out


def atividade_por_dia(p: Projeto) -> list[dict]:
    return linhas(p.con.execute("SELECT substr(momento, 1, 10) AS dia, COUNT(*) AS n, SUM(origem = 'ia') AS ia FROM historico"
                                " GROUP BY dia ORDER BY dia"))


# ------------------------------------------------------------------ exploração lexical (apoio à familiarização)
def _stopwords() -> set[str]:
    arq = config.pasta_recursos() / "stopwords_pt.txt"
    base = set()
    if arq.is_file():
        base = {dobrar(l.strip()) for l in arq.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
    return base


def _fala_participante(texto: str, somente_participante: bool) -> str:
    if not somente_participante:
        return texto
    from ..textos import turnos
    partes = []
    for t in turnos(texto):
        if t.locutor and re.match(r"ENTREVISTADOR", t.locutor):
            continue
        partes.append(texto[t.inicio_texto:t.fim])
    return "\n".join(partes) if partes else texto


def frequencia_palavras(p: Projeto, documento: int | None = None, somente_participante: bool = True,
                        n: int = 60, ngrama: int = 1) -> list[dict]:
    sw = _stopwords()
    cond = "AND t.documento_id = ?" if documento else ""
    params = [documento] if documento else []
    cont, docs = Counter(), {}
    for r in p.con.execute(f"SELECT t.documento_id, t.texto FROM transcricao_versoes t WHERE t.numero = (SELECT MAX(numero)"
                           f" FROM transcricao_versoes x WHERE x.documento_id = t.documento_id) {cond}", params):
        txt = _fala_participante(r[1], somente_participante)
        txt = re.sub(r"^[^\n]{1,80}? - \d{1,2}:\s*\d{2}[^\n]*$", " ", txt, flags=re.M)
        toks = [w for w in re.findall(r"[a-zà-ÿ]+", txt.lower()) if len(w) > 2 and dobrar(w) not in sw
                and not re.fullmatch(r"(participante|pessoa|local|entrevistador)", w)]
        grams = [" ".join(toks[i:i + ngrama]) for i in range(len(toks) - ngrama + 1)]
        cont.update(grams)
        for g in set(grams):
            docs[g] = docs.get(g, 0) + 1
    return [{"termo": t, "ocorrencias": c, "documentos": docs.get(t, 0)} for t, c in cont.most_common(n)]


def kwic(p: Projeto, termo: str, largura: int = 70, limite: int = 300) -> list[dict]:
    if not termo.strip():
        return []
    rx = re.compile(r"(?<!\w)" + re.escape(termo.strip()) + r"(?!\w)", re.I)
    out = []
    for r in p.con.execute("SELECT t.documento_id, d.codigo, pa.codigo, t.texto FROM transcricao_versoes t JOIN documentos d"
                           " ON d.id = t.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE d.ativo = 1"
                           " AND t.numero = (SELECT MAX(numero) FROM transcricao_versoes x WHERE x.documento_id = t.documento_id)"
                           " ORDER BY d.codigo"):
        for m in rx.finditer(r[3]):
            out.append({"documento_id": r[0], "documento": r[1], "participante": r[2], "inicio": m.start(), "fim": m.end(),
                        "esquerda": " ".join(r[3][max(0, m.start() - largura):m.start()].split()),
                        "termo": m.group(0), "direita": " ".join(r[3][m.end():m.end() + largura].split())})
            if len(out) >= limite:
                return out
    return out
