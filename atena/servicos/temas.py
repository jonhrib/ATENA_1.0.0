"""Fases 3, 4 e 5 — busca, revisão, definição e nomeação de temas.

Ciclo de um tema: ``candidato`` → ``em_revisao`` → ``revisado`` → ``definido``.
Também pode ser ``descartado`` ou ``fundido`` em outro tema. Qualquer estado
pode ser reaberto. O sistema sinaliza possíveis problemas (sobreposição,
pouca sustentação, concentração em um participante), mas nunca decide.
"""
from __future__ import annotations

from itertools import combinations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from .codificacao import PALETA
from .projetos import ErroValidacao, Projeto

SITUACOES = {"candidato": "Tema candidato", "em_revisao": "Em revisão", "revisado": "Revisado",
             "definido": "Definido", "descartado": "Descartado", "fundido": "Fundido em outro tema"}

CAMPOS_DEFINICAO = ("nome", "descricao", "conceito_central", "escopo", "limites", "relacao_pergunta", "justificativa")

VIVOS = "('candidato','em_revisao','revisado','definido')"


def _fase(p, n):
    p.con.execute("UPDATE fases SET situacao = 'em_andamento', iniciada_em = COALESCE(iniciada_em, ?), atualizado_em = ?"
                  " WHERE numero = ? AND situacao = 'nao_iniciada'", (agora(), agora(), n))


def obter(p: Projeto, tid: int) -> dict:
    t = linha(p.con.execute("SELECT * FROM temas WHERE id = ?", (tid,)).fetchone())
    if not t:
        raise ErroValidacao("Tema não encontrado.")
    t["situacao_rotulo"] = SITUACOES.get(t["situacao"], t["situacao"])
    t["codigos"] = linhas(p.con.execute(
        "SELECT c.id, c.nome, c.descricao, c.cor, tc.id AS tc_id, tc.justificativa AS tc_justificativa, tc.criado_em AS tc_criado"
        " FROM tema_codigos tc JOIN codigos c ON c.id = tc.codigo_id WHERE tc.tema_id = ? AND tc.ativo = 1"
        " AND c.situacao = 'ativo' ORDER BY c.nome COLLATE NOCASE", (tid,)))
    t["subtemas"] = linhas(p.con.execute(f"SELECT * FROM temas WHERE pai_id = ? AND situacao IN {VIVOS} ORDER BY nome", (tid,)))
    t["pai"] = linha(p.con.execute("SELECT id, nome FROM temas WHERE id = ?", (t["pai_id"],)).fetchone()) if t["pai_id"] else None
    t["revisoes"] = linhas(p.con.execute("SELECT * FROM revisoes_tema WHERE tema_id = ? ORDER BY id DESC", (tid,)))
    t["evidencias_representativas"] = linhas(p.con.execute(
        "SELECT te.id AS evid_id, te.comentario, f.*, d.codigo AS documento_codigo, pa.codigo AS participante_codigo"
        " FROM tema_evidencias te JOIN fragmentos f ON f.id = te.fragmento_id JOIN documentos d ON d.id = f.documento_id"
        " LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE te.tema_id = ? AND te.ativo = 1 AND f.ativo = 1"
        " ORDER BY d.codigo, f.inicio", (tid,)))
    return t


def listar(p: Projeto, incluir_inativos: bool = False) -> list[dict]:
    cond = "" if incluir_inativos else f"WHERE situacao IN {VIVOS}"
    out = linhas(p.con.execute(f"SELECT * FROM temas {cond} ORDER BY COALESCE(pai_id, id), pai_id IS NOT NULL, nome"))
    stats = estatisticas(p)
    for t in out:
        t["situacao_rotulo"] = SITUACOES.get(t["situacao"], t["situacao"])
        t.update(stats.get(t["id"], {"n_codigos": 0, "n_fragmentos": 0, "n_documentos": 0, "n_participantes": 0}))
    return out


def arvore(p: Projeto) -> list[dict]:
    todos = listar(p)
    cods = {}
    for r in p.con.execute("SELECT tc.tema_id, c.id, c.nome, c.cor FROM tema_codigos tc JOIN codigos c ON c.id = tc.codigo_id"
                           " WHERE tc.ativo = 1 AND c.situacao = 'ativo' ORDER BY c.nome COLLATE NOCASE"):
        cods.setdefault(r[0], []).append({"id": r[1], "nome": r[2], "cor": r[3]})
    for t in todos:
        t["codigos"] = cods.get(t["id"], [])
    raiz = [t for t in todos if not t["pai_id"]]
    for t in raiz:
        t["subtemas"] = [s for s in todos if s["pai_id"] == t["id"]]
    orfaos = [s for s in todos if s["pai_id"] and not any(r["id"] == s["pai_id"] for r in raiz)]
    return raiz + orfaos


def criar(p: Projeto, nome: str, descricao: str = "", *, pai_id: int | None = None, justificativa: str = "",
          origem: str = "manual", codigo_ids: list[int] = ()) -> dict:
    nome = " ".join((nome or "").split())
    if not nome:
        raise ErroValidacao("Informe o nome (provisório) do tema.")
    if pai_id:
        pai = obter(p, pai_id)
        if pai["pai_id"]:
            raise ErroValidacao("Subtemas não podem ter subtemas; escolha um tema principal como pai.")
    n = p.con.execute("SELECT COUNT(*) FROM temas").fetchone()[0]
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO temas(pai_id, nome, descricao, justificativa, origem, cor, criado_em, atualizado_em)"
                            " VALUES (?,?,?,?,?,?,?,?)", (pai_id or None, nome, descricao, justificativa, origem,
                                                          PALETA[(n * 5 + 3) % len(PALETA)], momento, momento))
        tid = cur.lastrowid
        registrar(p.con, "tema", tid, "criar_subtema" if pai_id else "criar", fase=3,
                  depois={"nome": nome, "descricao": descricao, "pai_id": pai_id, "origem": origem},
                  justificativa=justificativa, origem="ia" if origem == "ia" else "pesquisador")
        for cid in codigo_ids:
            atribuir_codigo(p, tid, int(cid), justificativa)
        _fase(p, 3)
    return obter(p, tid)


def editar(p: Projeto, tid: int, dados: dict, motivo: str = "") -> None:
    t = obter(p, tid)
    novos = {}
    for k in CAMPOS_DEFINICAO + ("cor",):
        if k in dados and dados[k] is not None:
            v = dados[k].strip() if isinstance(dados[k], str) else dados[k]
            if k == "nome" and not v:
                raise ErroValidacao("O nome do tema não pode ficar vazio.")
            if v != t[k]:
                novos[k] = v
    if not novos:
        return
    with transacao(p.con):
        if t["definicao_confirmada_em"] and any(k in novos for k in CAMPOS_DEFINICAO):
            novos["definicao_desatualizada"] = 1
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE temas SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), tid])
        registrar(p.con, "tema", tid, "editar", fase=_fase_do_tema(t), antes={k: t.get(k) for k in novos}, depois=novos,
                  justificativa=motivo)


def _fase_do_tema(t):
    return {"candidato": 3, "em_revisao": 4, "revisado": 4, "definido": 5}.get(t["situacao"], 3)


def _desatualizar(p, tid, motivo):
    r = p.con.execute("SELECT definicao_confirmada_em FROM temas WHERE id = ?", (tid,)).fetchone()
    if r and r[0]:
        p.con.execute("UPDATE temas SET definicao_desatualizada = 1 WHERE id = ?", (tid,))
        registrar(p.con, "tema", tid, "sinalizar_definicao_desatualizada", origem="sistema",
                  justificativa=f"Alteração após a definição confirmada ({motivo}); reconfirme a definição.")


def atribuir_codigo(p: Projeto, tid: int, cid: int, justificativa: str = "") -> int:
    t = obter(p, tid)
    if t["situacao"] in ("descartado", "fundido"):
        raise ErroValidacao("Não é possível atribuir códigos a um tema descartado ou fundido.")
    c = p.con.execute("SELECT nome, situacao FROM codigos WHERE id = ?", (cid,)).fetchone()
    if not c or c["situacao"] != "ativo":
        raise ErroValidacao("Código inválido ou inativo.")
    ja = p.con.execute("SELECT id FROM tema_codigos WHERE tema_id = ? AND codigo_id = ? AND ativo = 1", (tid, cid)).fetchone()
    if ja:
        return ja[0]
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO tema_codigos(tema_id, codigo_id, justificativa, criado_em) VALUES (?,?,?,?)",
                            (tid, cid, justificativa, agora()))
        registrar(p.con, "tema", tid, "agrupar_codigo", fase=_fase_do_tema(t),
                  depois={"tema": t["nome"], "codigo_id": cid, "codigo": c["nome"]}, justificativa=justificativa)
        _desatualizar(p, tid, f"código “{c['nome']}” incluído")
        _fase(p, 3)
        return cur.lastrowid


def remover_codigo(p: Projeto, tid: int, cid: int, justificativa: str = "") -> None:
    r = p.con.execute("SELECT tc.id, c.nome FROM tema_codigos tc JOIN codigos c ON c.id = tc.codigo_id"
                      " WHERE tc.tema_id = ? AND tc.codigo_id = ? AND tc.ativo = 1", (tid, cid)).fetchone()
    if not r:
        raise ErroValidacao("O código não está neste tema.")
    t = obter(p, tid)
    with transacao(p.con):
        p.con.execute("UPDATE tema_codigos SET ativo = 0, removido_em = ? WHERE id = ?", (agora(), r["id"]))
        registrar(p.con, "tema", tid, "retirar_codigo", fase=_fase_do_tema(t),
                  antes={"tema": t["nome"], "codigo_id": cid, "codigo": r["nome"]}, justificativa=justificativa)
        _desatualizar(p, tid, f"código “{r['nome']}” retirado")


def mover_codigo(p: Projeto, cid: int, de_tid: int | None, para_tid: int | None, justificativa: str = "") -> None:
    with transacao(p.con):
        if de_tid:
            remover_codigo(p, de_tid, cid, justificativa or "reorganização no mapa temático")
        if para_tid:
            atribuir_codigo(p, para_tid, cid, justificativa or "reorganização no mapa temático")


def definir_pai(p: Projeto, tid: int, pai_id: int | None, justificativa: str = "") -> None:
    t = obter(p, tid)
    if pai_id:
        if int(pai_id) == tid:
            raise ErroValidacao("Um tema não pode ser subtema de si mesmo.")
        pai = obter(p, int(pai_id))
        if pai["pai_id"]:
            raise ErroValidacao("O tema escolhido já é um subtema.")
        if t["subtemas"]:
            raise ErroValidacao("Este tema tem subtemas; reorganize-os antes de transformá-lo em subtema.")
    with transacao(p.con):
        p.con.execute("UPDATE temas SET pai_id = ?, atualizado_em = ? WHERE id = ?", (pai_id or None, agora(), tid))
        registrar(p.con, "tema", tid, "reorganizar_hierarquia", fase=_fase_do_tema(t),
                  antes={"pai_id": t["pai_id"]}, depois={"pai_id": pai_id or None}, justificativa=justificativa)


def alterar_situacao(p: Projeto, tid: int, situacao: str, justificativa: str = "") -> None:
    if situacao not in ("candidato", "em_revisao", "revisado", "definido", "descartado"):
        raise ErroValidacao("Situação inválida.")
    t = obter(p, tid)
    if situacao == t["situacao"]:
        return
    if situacao in ("descartado",) and not justificativa.strip():
        raise ErroValidacao("Justifique o descarte do tema.")
    if situacao == "definido" and not t["definicao_confirmada_em"]:
        raise ErroValidacao("Use a tela de definição (fase 5) para confirmar a definição do tema.")
    with transacao(p.con):
        p.con.execute("UPDATE temas SET situacao = ?, atualizado_em = ? WHERE id = ?", (situacao, agora(), tid))
        if situacao == "descartado":
            for s in t["subtemas"]:
                p.con.execute("UPDATE temas SET pai_id = NULL WHERE id = ?", (s["id"],))
        registrar(p.con, "tema", tid, "descartar" if situacao == "descartado" else
                  ("reabrir" if t["situacao"] in ("descartado", "definido", "revisado") else "alterar_situacao"),
                  fase=4 if situacao in ("em_revisao", "revisado", "descartado") else _fase_do_tema(t),
                  antes={"situacao": t["situacao"]}, depois={"situacao": situacao}, justificativa=justificativa)
        if situacao in ("em_revisao", "revisado", "descartado"):
            _fase(p, 4)


def reativar(p: Projeto, tid: int, justificativa: str = "") -> None:
    t = obter(p, tid)
    if t["situacao"] not in ("descartado", "fundido"):
        return
    with transacao(p.con):
        p.con.execute("UPDATE temas SET situacao = 'em_revisao', fundido_em = NULL, atualizado_em = ? WHERE id = ?", (agora(), tid))
        registrar(p.con, "tema", tid, "reativar", fase=4, antes={"situacao": t["situacao"]},
                  depois={"situacao": "em_revisao"}, justificativa=justificativa)


def fundir(p: Projeto, origens: list[int], destino: int, justificativa: str) -> None:
    if not justificativa.strip():
        raise ErroValidacao("Justifique a fusão dos temas.")
    dest = obter(p, destino)
    origens = [int(o) for o in origens if int(o) != destino]
    if not origens:
        raise ErroValidacao("Escolha ao menos um tema para fundir no destino.")
    with transacao(p.con):
        for o in origens:
            t = obter(p, o)
            for c in t["codigos"]:
                atribuir_codigo(p, destino, c["id"], f"fusão do tema “{t['nome']}”")
                p.con.execute("UPDATE tema_codigos SET ativo = 0, removido_em = ? WHERE id = ?", (agora(), c["tc_id"]))
            for e in t["evidencias_representativas"]:
                marcar_evidencia(p, destino, e["id"], e["comentario"])
            for s in t["subtemas"]:
                p.con.execute("UPDATE temas SET pai_id = ? WHERE id = ?", (None if dest["pai_id"] else destino, s["id"]))
            p.con.execute("UPDATE temas SET situacao = 'fundido', fundido_em = ?, atualizado_em = ? WHERE id = ?",
                          (destino, agora(), o))
            registrar(p.con, "tema", o, "fundir", fase=4,
                      antes={"nome": t["nome"], "situacao": t["situacao"], "codigos": [c["nome"] for c in t["codigos"]]},
                      depois={"situacao": "fundido", "destino": destino, "destino_nome": dest["nome"]},
                      justificativa=justificativa)
        _fase(p, 4)


def dividir(p: Projeto, tid: int, novo_nome: str, codigo_ids: list[int], justificativa: str,
            como_subtema: bool = False) -> dict:
    t = obter(p, tid)
    if not justificativa.strip():
        raise ErroValidacao("Justifique a divisão do tema.")
    ids = {int(c) for c in codigo_ids}
    if not ids:
        raise ErroValidacao("Selecione os códigos que formarão o novo tema.")
    if not ids <= {c["id"] for c in t["codigos"]}:
        raise ErroValidacao("Só é possível mover códigos que pertencem ao tema dividido.")
    with transacao(p.con):
        novo = criar(p, novo_nome, f"Dividido de “{t['nome']}”.",
                     pai_id=(tid if como_subtema and not t["pai_id"] else t["pai_id"]), justificativa=justificativa)
        for cid in ids:
            atribuir_codigo(p, novo["id"], cid, f"divisão do tema “{t['nome']}”")
            if not como_subtema:
                remover_codigo(p, tid, cid, f"divisão: movido para “{novo_nome}”")
        p.con.execute("UPDATE temas SET situacao = 'em_revisao' WHERE id = ?", (novo["id"],))
        registrar(p.con, "tema", tid, "dividir", fase=4,
                  depois={"novo_tema": novo["id"], "novo_nome": novo_nome, "codigos_movidos": sorted(ids),
                          "como_subtema": como_subtema}, justificativa=justificativa)
    return obter(p, novo["id"])


def revisar(p: Projeto, tid: int, nivel: int, *, coerencia: str = "", distincao: str = "", sobreposicao: str = "",
            decisao: str = "", observacao: str = "") -> None:
    t = obter(p, tid)
    nivel = int(nivel)
    if nivel not in (1, 2):
        raise ErroValidacao("Nível de revisão inválido.")
    if not any(x.strip() for x in (coerencia, distincao, sobreposicao, observacao)):
        raise ErroValidacao("Registre ao menos uma observação da revisão.")
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO revisoes_tema(tema_id, nivel, coerencia, distincao, sobreposicao, decisao, observacao,"
                            " criado_em) VALUES (?,?,?,?,?,?,?,?)",
                            (tid, nivel, coerencia, distincao, sobreposicao, decisao, observacao, agora()))
        if t["situacao"] == "candidato":
            p.con.execute("UPDATE temas SET situacao = 'em_revisao', atualizado_em = ? WHERE id = ?", (agora(), tid))
        if decisao == "manter" and t["situacao"] in ("candidato", "em_revisao"):
            niveis = {r["nivel"] for r in t["revisoes"]} | {nivel}
            if niveis >= {1, 2}:
                p.con.execute("UPDATE temas SET situacao = 'revisado', atualizado_em = ? WHERE id = ?", (agora(), tid))
        registrar(p.con, "revisao_tema", cur.lastrowid, "revisar_tema", fase=4,
                  depois={"tema_id": tid, "tema": t["nome"], "nivel": nivel, "coerencia": coerencia,
                          "distincao": distincao, "sobreposicao": sobreposicao, "decisao": decisao},
                  justificativa=observacao)
        _fase(p, 4)


def definir(p: Projeto, tid: int, dados: dict, confirmar: bool = False) -> dict:
    """Fase 5: grava a definição; com ``confirmar`` registra a confirmação do pesquisador."""
    editar(p, tid, {k: dados.get(k) for k in CAMPOS_DEFINICAO if k in dados}, motivo="definição do tema (fase 5)")
    t = obter(p, tid)
    pendencias = []
    if not t["conceito_central"].strip():
        pendencias.append("descrever o conceito central")
    if not t["descricao"].strip():
        pendencias.append("escrever a descrição")
    if not t["codigos"] and not t["subtemas"]:
        pendencias.append("associar ao menos um código ou subtema")
    if not t["evidencias_representativas"]:
        pendencias.append("selecionar ao menos uma evidência representativa")
    if confirmar:
        if pendencias:
            raise ErroValidacao("Para confirmar a definição é preciso: " + "; ".join(pendencias) + ".")
        with transacao(p.con):
            p.con.execute("UPDATE temas SET situacao = 'definido', definicao_confirmada_em = ?, definicao_desatualizada = 0,"
                          " atualizado_em = ? WHERE id = ?", (agora(), agora(), tid))
            registrar(p.con, "tema", tid, "confirmar_definicao", fase=5,
                      depois={k: t[k] for k in CAMPOS_DEFINICAO} | {
                          "codigos": [c["nome"] for c in t["codigos"]], "subtemas": [s["nome"] for s in t["subtemas"]],
                          "evidencias": [e["id"] for e in t["evidencias_representativas"]]},
                      justificativa=t["justificativa"])
            _fase(p, 5)
    return {"pendencias": pendencias, "tema": obter(p, tid)}


def marcar_evidencia(p: Projeto, tid: int, fid: int, comentario: str = "") -> None:
    if p.con.execute("SELECT 1 FROM tema_evidencias WHERE tema_id = ? AND fragmento_id = ? AND ativo = 1", (tid, fid)).fetchone():
        return
    f = p.con.execute("SELECT texto FROM fragmentos WHERE id = ? AND ativo = 1", (fid,)).fetchone()
    if not f:
        raise ErroValidacao("Fragmento não encontrado.")
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO tema_evidencias(tema_id, fragmento_id, comentario, criado_em) VALUES (?,?,?,?)",
                            (tid, fid, comentario, agora()))
        registrar(p.con, "evidencia", cur.lastrowid, "marcar_representativa", fase=5,
                  depois={"tema_id": tid, "fragmento_id": fid, "trecho": f["texto"][:300], "comentario": comentario})
        _desatualizar(p, tid, "evidência representativa incluída")


def desmarcar_evidencia(p: Projeto, tid: int, fid: int, justificativa: str = "") -> None:
    with transacao(p.con):
        r = p.con.execute("SELECT id FROM tema_evidencias WHERE tema_id = ? AND fragmento_id = ? AND ativo = 1", (tid, fid)).fetchone()
        if not r:
            return
        p.con.execute("UPDATE tema_evidencias SET ativo = 0 WHERE id = ?", (r[0],))
        registrar(p.con, "evidencia", r[0], "desmarcar_representativa", fase=5,
                  antes={"tema_id": tid, "fragmento_id": fid}, justificativa=justificativa)
        _desatualizar(p, tid, "evidência representativa retirada")


def ids_codigos_tema(p: Projeto, tid: int, incluir_subtemas: bool = True) -> set[int]:
    ids = {r[0] for r in p.con.execute("SELECT tc.codigo_id FROM tema_codigos tc JOIN codigos c ON c.id = tc.codigo_id"
                                       " WHERE tc.tema_id = ? AND tc.ativo = 1 AND c.situacao = 'ativo'", (tid,))}
    if incluir_subtemas:
        for s in p.con.execute(f"SELECT id FROM temas WHERE pai_id = ? AND situacao IN {VIVOS}", (tid,)):
            ids |= ids_codigos_tema(p, s[0], False)
    return ids


def evidencias(p: Projeto, tid: int) -> list[dict]:
    ids = ids_codigos_tema(p, tid)
    if not ids:
        return []
    marc = ",".join("?" * len(ids))
    out = linhas(p.con.execute(
        f"SELECT DISTINCT f.*, d.codigo AS documento_codigo, d.titulo AS documento_titulo, pa.codigo AS participante_codigo"
        f" FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id JOIN documentos d ON d.id = f.documento_id"
        f" LEFT JOIN participantes pa ON pa.id = d.participante_id"
        f" WHERE cf.codigo_id IN ({marc}) AND cf.ativo = 1 AND f.ativo = 1 ORDER BY d.codigo, f.inicio", list(ids)))
    rep = {r[0] for r in p.con.execute("SELECT fragmento_id FROM tema_evidencias WHERE tema_id = ? AND ativo = 1", (tid,))}
    for f in out:
        f["codigos"] = [{"id": r[0], "nome": r[1], "cor": r[2], "no_tema": r[0] in ids} for r in p.con.execute(
            "SELECT c.id, c.nome, c.cor FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id"
            " WHERE cf.fragmento_id = ? AND cf.ativo = 1 AND c.situacao = 'ativo'", (f["id"],))]
        f["representativa"] = f["id"] in rep
    return out


def estatisticas(p: Projeto) -> dict[int, dict]:
    out = {}
    for t in p.con.execute(f"SELECT id FROM temas WHERE situacao IN {VIVOS}"):
        tid = t[0]
        ids = ids_codigos_tema(p, tid)
        if ids:
            marc = ",".join("?" * len(ids))
            r = p.con.execute(f"SELECT COUNT(DISTINCT f.id), COUNT(DISTINCT f.documento_id), COUNT(DISTINCT d.participante_id)"
                              f" FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id"
                              f" JOIN documentos d ON d.id = f.documento_id"
                              f" WHERE cf.codigo_id IN ({marc}) AND cf.ativo = 1 AND f.ativo = 1", list(ids)).fetchone()
        else:
            r = (0, 0, 0)
        out[tid] = {"n_codigos": len(ids), "n_fragmentos": r[0], "n_documentos": r[1], "n_participantes": r[2]}
    return out


def codigos_sem_tema(p: Projeto) -> list[dict]:
    return linhas(p.con.execute(
        f"SELECT c.* FROM codigos c WHERE c.situacao = 'ativo' AND NOT EXISTS (SELECT 1 FROM tema_codigos tc"
        f" JOIN temas t ON t.id = tc.tema_id WHERE tc.codigo_id = c.id AND tc.ativo = 1 AND t.situacao IN {VIVOS})"
        f" ORDER BY c.nome COLLATE NOCASE"))


def diagnostico(p: Projeto) -> dict:
    """Sinais para a revisão (fase 4). Apenas informativos — o pesquisador decide."""
    temas = listar(p)
    frag_por_tema = {}
    for t in temas:
        frag_por_tema[t["id"]] = {f["id"] for f in evidencias(p, t["id"])}
    sinais = []
    pares = []
    for a, b in combinations([t for t in temas], 2):
        if a["pai_id"] == b["id"] or b["pai_id"] == a["id"]:
            continue
        ca, cb = ids_codigos_tema(p, a["id"], False), ids_codigos_tema(p, b["id"], False)
        comuns = ca & cb
        fa, fb = frag_por_tema[a["id"]], frag_por_tema[b["id"]]
        jac = len(fa & fb) / len(fa | fb) if (fa | fb) else 0
        if comuns or jac >= 0.25:
            pares.append({"a": a, "b": b, "codigos_comuns": len(comuns), "jaccard": round(jac, 2)})
    for t in temas:
        if t["n_codigos"] == 0:
            sinais.append({"tema": t, "tipo": "sem_codigos", "texto": "Tema sem códigos associados."})
        elif t["n_codigos"] == 1 and not t["pai_id"]:
            sinais.append({"tema": t, "tipo": "um_codigo",
                           "texto": "Tema sustentado por um único código — avalie se é um tema ou um código."})
        if t["n_fragmentos"] and t["n_participantes"] == 1:
            sinais.append({"tema": t, "tipo": "um_participante",
                           "texto": "Todas as evidências vêm de um único participante."})
        niveis = {r[0] for r in p.con.execute("SELECT nivel FROM revisoes_tema WHERE tema_id = ?", (t["id"],))}
        if t["situacao"] in ("candidato", "em_revisao") and niveis != {1, 2}:
            falta = sorted({1, 2} - niveis)
            sinais.append({"tema": t, "tipo": "revisao_pendente",
                           "texto": "Revisão de nível " + " e ".join(map(str, falta)) + " ainda não registrada."})
        if t["definicao_desatualizada"]:
            sinais.append({"tema": t, "tipo": "definicao_desatualizada",
                           "texto": "Houve alterações depois da definição confirmada."})
    return {"sobreposicoes": sorted(pares, key=lambda x: -x["jaccard"]), "sinais": sinais,
            "codigos_sem_tema": codigos_sem_tema(p)}


def confronto_documentos(p: Projeto, tid: int) -> list[dict]:
    """Distribuição das evidências do tema pelas entrevistas (inclui as que não têm nenhuma)."""
    ev = evidencias(p, tid)
    cont = {}
    for f in ev:
        cont[f["documento_id"]] = cont.get(f["documento_id"], 0) + 1
    out = []
    for d in linhas(p.con.execute("SELECT d.id, d.codigo, d.titulo, pa.codigo AS participante FROM documentos d"
                                  " LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE d.ativo = 1"
                                  " AND d.situacao = 'anonimizado' ORDER BY d.codigo")):
        d["n"] = cont.get(d["id"], 0)
        out.append(d)
    return out
