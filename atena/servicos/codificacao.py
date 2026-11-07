"""Fase 2 — fragmentos, códigos e codificações.

Um **fragmento** é um trecho selecionado de uma versão anonimizada, guardado com
posição (início/fim), texto exato e contexto. Um fragmento pode ter vários
códigos; um código pode ter vários fragmentos. Nada é apagado: exclusões são
lógicas e registradas no histórico.
"""
from __future__ import annotations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from .projetos import ErroValidacao, Projeto

PALETA = ["#0f766e", "#6d28d9", "#b45309", "#be123c", "#1d4ed8", "#15803d", "#a21caf", "#0e7490",
          "#c2410c", "#4338ca", "#4d7c0f", "#9f1239", "#0369a1", "#7c2d12", "#5b21b6", "#047857"]


# ------------------------------------------------------------------ fragmentos
def _versao(p, did):
    v = p.con.execute("SELECT * FROM transcricao_versoes WHERE documento_id = ? ORDER BY numero DESC LIMIT 1",
                      (did,)).fetchone()
    if not v:
        raise ErroValidacao("O documento ainda não tem versão anonimizada. Conclua a anonimização antes de codificar.")
    return v


def criar_fragmento(p: Projeto, did: int, inicio: int, fim: int, *, relevante: bool = False, nota: str = "",
                    texto_esperado: str | None = None) -> dict:
    v = _versao(p, did)
    texto = v["texto"]
    inicio, fim = int(inicio), int(fim)
    if not (0 <= inicio < fim <= len(texto)):
        raise ErroValidacao("Seleção fora do texto.")
    while inicio < fim and texto[inicio].isspace():
        inicio += 1
    while fim > inicio and texto[fim - 1].isspace():
        fim -= 1
    if fim <= inicio:
        raise ErroValidacao("Selecione um trecho com texto.")
    trecho = texto[inicio:fim]
    if texto_esperado is not None and " ".join(texto_esperado.split()) != " ".join(trecho.split()):
        raise ErroValidacao("A seleção não corresponde ao texto atual (a transcrição pode ter sido alterada). "
                            "Recarregue a página e selecione novamente.")
    existente = p.con.execute("SELECT id FROM fragmentos WHERE documento_id = ? AND inicio = ? AND fim = ? AND ativo = 1",
                              (did, inicio, fim)).fetchone()
    if existente:
        if relevante:
            p.con.execute("UPDATE fragmentos SET relevante = 1 WHERE id = ?", (existente[0],))
        return obter_fragmento(p, existente[0])
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute(
            "INSERT INTO fragmentos(documento_id, versao_id, inicio, fim, texto, prefixo, sufixo, relevante, nota,"
            " criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (did, v["id"], inicio, fim, trecho, texto[max(0, inicio - 40):inicio], texto[fim:fim + 40],
             1 if relevante else 0, nota, momento, momento))
        fid = cur.lastrowid
        registrar(p.con, "fragmento", fid, "criar", fase=2 if not relevante else 1,
                  depois={"documento_id": did, "versao": v["numero"], "inicio": inicio, "fim": fim, "texto": trecho,
                          "relevante": bool(relevante), "nota": nota})
    return obter_fragmento(p, fid)


def obter_fragmento(p: Projeto, fid: int) -> dict:
    f = linha(p.con.execute(
        "SELECT f.*, d.codigo AS documento_codigo, d.titulo AS documento_titulo, pa.codigo AS participante_codigo"
        " FROM fragmentos f JOIN documentos d ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id"
        " WHERE f.id = ?", (fid,)).fetchone())
    if not f:
        raise ErroValidacao("Fragmento não encontrado.")
    f["codigos"] = linhas(p.con.execute(
        "SELECT c.id, c.nome, c.cor, c.situacao, cf.id AS codificacao_id, cf.justificativa, cf.origem, cf.criado_em"
        " FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id WHERE cf.fragmento_id = ? AND cf.ativo = 1"
        " ORDER BY c.nome COLLATE NOCASE", (fid,)))
    f["temas"] = linhas(p.con.execute(
        "SELECT DISTINCT t.id, t.nome, t.situacao FROM tema_codigos tc JOIN temas t ON t.id = tc.tema_id"
        " JOIN codificacoes cf ON cf.codigo_id = tc.codigo_id"
        " WHERE cf.fragmento_id = ? AND cf.ativo = 1 AND tc.ativo = 1 AND t.situacao NOT IN ('descartado','fundido')", (fid,)))
    f["notas"] = linhas(p.con.execute("SELECT * FROM notas WHERE fragmento_id = ? AND ativo = 1 ORDER BY id DESC", (fid,)))
    return f


def fragmentos_documento(p: Projeto, did: int) -> list[dict]:
    out = linhas(p.con.execute("SELECT * FROM fragmentos WHERE documento_id = ? AND ativo = 1 ORDER BY inicio", (did,)))
    cods = {}
    for r in p.con.execute("SELECT cf.fragmento_id, c.id, c.nome, c.cor FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id"
                           " JOIN fragmentos f ON f.id = cf.fragmento_id WHERE f.documento_id = ? AND cf.ativo = 1"
                           " AND c.situacao = 'ativo'", (did,)):
        cods.setdefault(r[0], []).append({"id": r[1], "nome": r[2], "cor": r[3]})
    for f in out:
        f["codigos"] = cods.get(f["id"], [])
    return out


def editar_fragmento(p: Projeto, fid: int, *, nota: str | None = None, relevante: bool | None = None) -> None:
    f = obter_fragmento(p, fid)
    novos = {}
    if nota is not None and nota != f["nota"]:
        novos["nota"] = nota
    if relevante is not None and int(relevante) != f["relevante"]:
        novos["relevante"] = int(relevante)
    if not novos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE fragmentos SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), fid])
        registrar(p.con, "fragmento", fid, "editar", antes={k: f[k] for k in novos}, depois=novos)


def desativar_fragmento(p: Projeto, fid: int, justificativa: str) -> None:
    f = obter_fragmento(p, fid)
    if not justificativa.strip():
        raise ErroValidacao("Informe o motivo para remover o fragmento.")
    with transacao(p.con):
        p.con.execute("UPDATE fragmentos SET ativo = 0, atualizado_em = ? WHERE id = ?", (agora(), fid))
        p.con.execute("UPDATE codificacoes SET ativo = 0, removido_em = ? WHERE fragmento_id = ? AND ativo = 1", (agora(), fid))
        p.con.execute("UPDATE tema_evidencias SET ativo = 0 WHERE fragmento_id = ?", (fid,))
        registrar(p.con, "fragmento", fid, "desativar",
                  antes={"texto": f["texto"], "codigos": [c["nome"] for c in f["codigos"]], "ativo": 1},
                  depois={"ativo": 0}, justificativa=justificativa)


def resolver_situacao(p: Projeto, fid: int, acao: str, *, inicio: int | None = None, fim: int | None = None,
                      justificativa: str = "") -> None:
    """Revisão de fragmento realocado/inconsistente pelo pesquisador."""
    f = obter_fragmento(p, fid)
    v = _versao(p, f["documento_id"])
    if acao == "confirmar":
        novo_texto = v["texto"][f["inicio"]:f["fim"]]
        with transacao(p.con):
            p.con.execute("UPDATE fragmentos SET situacao = 'ok', texto = ?, versao_id = ?, atualizado_em = ? WHERE id = ?",
                          (novo_texto, v["id"], agora(), fid))
            registrar(p.con, "fragmento", fid, "confirmar_associacao",
                      antes={"texto": f["texto"], "situacao": f["situacao"]},
                      depois={"texto": novo_texto, "situacao": "ok", "versao": v["numero"]}, justificativa=justificativa)
    elif acao == "reposicionar":
        if inicio is None or fim is None:
            raise ErroValidacao("Selecione o novo trecho.")
        inicio, fim = int(inicio), int(fim)
        if not (0 <= inicio < fim <= len(v["texto"])):
            raise ErroValidacao("Seleção inválida.")
        novo_texto = v["texto"][inicio:fim]
        with transacao(p.con):
            p.con.execute("UPDATE fragmentos SET inicio = ?, fim = ?, texto = ?, prefixo = ?, sufixo = ?, situacao = 'ok',"
                          " versao_id = ?, atualizado_em = ? WHERE id = ?",
                          (inicio, fim, novo_texto, v["texto"][max(0, inicio - 40):inicio], v["texto"][fim:fim + 40],
                           v["id"], agora(), fid))
            registrar(p.con, "fragmento", fid, "reposicionar",
                      antes={"inicio": f["inicio"], "fim": f["fim"], "texto": f["texto"], "situacao": f["situacao"]},
                      depois={"inicio": inicio, "fim": fim, "texto": novo_texto, "situacao": "ok"},
                      justificativa=justificativa)
    elif acao == "desativar":
        desativar_fragmento(p, fid, justificativa or "fragmento inconsistente removido após revisão")
    else:
        raise ErroValidacao("Ação inválida.")


def fragmentos_pendentes(p: Projeto) -> list[dict]:
    return linhas(p.con.execute(
        "SELECT f.*, d.codigo AS documento_codigo FROM fragmentos f JOIN documentos d ON d.id = f.documento_id"
        " WHERE f.ativo = 1 AND f.situacao != 'ok' ORDER BY f.situacao DESC, d.codigo, f.inicio"))


# ------------------------------------------------------------------ códigos
def _nome_em_uso(p, nome, exceto=None):
    r = p.con.execute("SELECT id FROM codigos WHERE nome = ? COLLATE NOCASE AND situacao = 'ativo'"
                      + (" AND id != ?" if exceto else ""), (nome, exceto) if exceto else (nome,)).fetchone()
    return r is not None


def criar_codigo(p: Projeto, nome: str, descricao: str = "", justificativa: str = "", origem: str = "manual",
                 cor: str = "") -> dict:
    nome = " ".join((nome or "").split())
    if not nome:
        raise ErroValidacao("Informe o nome do código.")
    if len(nome) > 120:
        raise ErroValidacao("Nome do código muito longo (máx. 120 caracteres).")
    if _nome_em_uso(p, nome):
        raise ErroValidacao(f"Já existe um código ativo chamado “{nome}”.")
    n = p.con.execute("SELECT COUNT(*) FROM codigos").fetchone()[0]
    cor = cor or PALETA[n % len(PALETA)]
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO codigos(nome, descricao, justificativa, origem, cor, criado_em, atualizado_em)"
                            " VALUES (?,?,?,?,?,?,?)", (nome, descricao, justificativa, origem, cor, momento, momento))
        cid = cur.lastrowid
        registrar(p.con, "codigo", cid, "criar", fase=2,
                  depois={"nome": nome, "descricao": descricao, "justificativa": justificativa, "origem": origem},
                  justificativa=justificativa, origem="ia" if origem == "ia" else "pesquisador")
        _marcar_fase_em_andamento(p, 2)
    return obter_codigo(p, cid)


def _marcar_fase_em_andamento(p, n):
    p.con.execute("UPDATE fases SET situacao = 'em_andamento', iniciada_em = COALESCE(iniciada_em, ?), atualizado_em = ?"
                  " WHERE numero = ? AND situacao = 'nao_iniciada'", (agora(), agora(), n))


def obter_codigo(p: Projeto, cid: int) -> dict:
    c = linha(p.con.execute("SELECT * FROM codigos WHERE id = ?", (cid,)).fetchone())
    if not c:
        raise ErroValidacao("Código não encontrado.")
    c["n_fragmentos"] = p.con.execute("SELECT COUNT(*) FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id"
                                      " WHERE cf.codigo_id = ? AND cf.ativo = 1 AND f.ativo = 1", (cid,)).fetchone()[0]
    c["temas"] = linhas(p.con.execute(
        "SELECT t.id, t.nome, t.situacao, t.pai_id FROM tema_codigos tc JOIN temas t ON t.id = tc.tema_id"
        " WHERE tc.codigo_id = ? AND tc.ativo = 1 AND t.situacao NOT IN ('descartado','fundido')", (cid,)))
    return c


def editar_codigo(p: Projeto, cid: int, *, nome: str | None = None, descricao: str | None = None,
                  justificativa: str | None = None, cor: str | None = None, motivo: str = "") -> None:
    c = obter_codigo(p, cid)
    novos = {}
    if nome is not None:
        nome = " ".join(nome.split())
        if not nome:
            raise ErroValidacao("O nome não pode ficar vazio.")
        if nome != c["nome"]:
            if _nome_em_uso(p, nome, cid):
                raise ErroValidacao(f"Já existe um código ativo chamado “{nome}”.")
            novos["nome"] = nome
    for k, v in (("descricao", descricao), ("justificativa", justificativa), ("cor", cor)):
        if v is not None and v != c[k]:
            novos[k] = v
    if not novos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE codigos SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), cid])
        registrar(p.con, "codigo", cid, "editar", fase=2, antes={k: c[k] for k in novos}, depois=novos, justificativa=motivo)
        _marcar_temas_desatualizados(p, cid, "código editado")


def _marcar_temas_desatualizados(p, cid, motivo):
    for t in p.con.execute("SELECT DISTINCT tema_id FROM tema_codigos WHERE codigo_id = ? AND ativo = 1", (cid,)):
        r = p.con.execute("SELECT definicao_confirmada_em FROM temas WHERE id = ?", (t[0],)).fetchone()
        if r and r[0]:
            p.con.execute("UPDATE temas SET definicao_desatualizada = 1 WHERE id = ?", (t[0],))
            registrar(p.con, "tema", t[0], "sinalizar_definicao_desatualizada", origem="sistema",
                      justificativa=f"A definição foi confirmada antes de uma alteração ({motivo}); reconfirme.")


def excluir_codigo(p: Projeto, cid: int, justificativa: str) -> None:
    c = obter_codigo(p, cid)
    if not justificativa.strip():
        raise ErroValidacao("Informe a justificativa da exclusão.")
    if c["situacao"] != "ativo":
        raise ErroValidacao("O código já não está ativo.")
    with transacao(p.con):
        p.con.execute("UPDATE codigos SET situacao = 'excluido', atualizado_em = ? WHERE id = ?", (agora(), cid))
        _marcar_temas_desatualizados(p, cid, "código excluído")
        registrar(p.con, "codigo", cid, "excluir", fase=2,
                  antes={"situacao": "ativo", "nome": c["nome"], "n_fragmentos": c["n_fragmentos"],
                         "temas": [t["nome"] for t in c["temas"]]},
                  depois={"situacao": "excluido"},
                  justificativa=justificativa + " (fragmentos e associações preservados; o código pode ser restaurado)")


def restaurar_codigo(p: Projeto, cid: int, justificativa: str = "") -> None:
    c = obter_codigo(p, cid)
    if c["situacao"] == "ativo":
        return
    if _nome_em_uso(p, c["nome"], cid):
        raise ErroValidacao(f"Já existe outro código ativo chamado “{c['nome']}”. Renomeie-o antes.")
    with transacao(p.con):
        p.con.execute("UPDATE codigos SET situacao = 'ativo', mesclado_em = NULL, atualizado_em = ? WHERE id = ?", (agora(), cid))
        registrar(p.con, "codigo", cid, "restaurar", fase=2, antes={"situacao": c["situacao"]},
                  depois={"situacao": "ativo"}, justificativa=justificativa)


def mesclar_codigos(p: Projeto, origens: list[int], destino: int, justificativa: str) -> None:
    if not justificativa.strip():
        raise ErroValidacao("Justifique a mesclagem.")
    dest = obter_codigo(p, destino)
    origens = [int(o) for o in origens if int(o) != destino]
    if not origens:
        raise ErroValidacao("Selecione ao menos um código para mesclar no destino.")
    with transacao(p.con):
        for o in origens:
            c = obter_codigo(p, o)
            movidas = []
            for cf in linhas(p.con.execute("SELECT * FROM codificacoes WHERE codigo_id = ? AND ativo = 1", (o,))):
                ja = p.con.execute("SELECT id FROM codificacoes WHERE fragmento_id = ? AND codigo_id = ? AND ativo = 1",
                                   (cf["fragmento_id"], destino)).fetchone()
                p.con.execute("UPDATE codificacoes SET ativo = 0, removido_em = ? WHERE id = ?", (agora(), cf["id"]))
                if not ja:
                    p.con.execute("INSERT INTO codificacoes(fragmento_id, codigo_id, justificativa, origem, criado_em)"
                                  " VALUES (?,?,?,?,?)", (cf["fragmento_id"], destino,
                                                          f"mesclado de “{c['nome']}”: {cf['justificativa'] or ''}".strip(": "),
                                                          cf["origem"], agora()))
                movidas.append(cf["fragmento_id"])
            for tc in linhas(p.con.execute("SELECT * FROM tema_codigos WHERE codigo_id = ? AND ativo = 1", (o,))):
                p.con.execute("UPDATE tema_codigos SET ativo = 0, removido_em = ? WHERE id = ?", (agora(), tc["id"]))
                if not p.con.execute("SELECT 1 FROM tema_codigos WHERE tema_id = ? AND codigo_id = ? AND ativo = 1",
                                     (tc["tema_id"], destino)).fetchone():
                    p.con.execute("INSERT INTO tema_codigos(tema_id, codigo_id, justificativa, criado_em) VALUES (?,?,?,?)",
                                  (tc["tema_id"], destino, f"herdado da mesclagem de “{c['nome']}”", agora()))
            p.con.execute("UPDATE codigos SET situacao = 'mesclado', mesclado_em = ?, atualizado_em = ? WHERE id = ?",
                          (destino, agora(), o))
            registrar(p.con, "codigo", o, "mesclar", fase=2,
                      antes={"nome": c["nome"], "situacao": "ativo", "fragmentos": movidas},
                      depois={"situacao": "mesclado", "destino": destino, "destino_nome": dest["nome"]},
                      justificativa=justificativa)


def listar_codigos(p: Projeto, situacao: str = "ativo") -> list[dict]:
    cond = "WHERE c.situacao = ?" if situacao else ""
    params = (situacao,) if situacao else ()
    out = linhas(p.con.execute(f"SELECT c.* FROM codigos c {cond} ORDER BY c.nome COLLATE NOCASE", params))
    contagens = {r[0]: (r[1], r[2], r[3]) for r in p.con.execute(
        "SELECT cf.codigo_id, COUNT(DISTINCT f.id), COUNT(DISTINCT f.documento_id), COUNT(DISTINCT d.participante_id)"
        " FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id JOIN documentos d ON d.id = f.documento_id"
        " WHERE cf.ativo = 1 AND f.ativo = 1 GROUP BY cf.codigo_id")}
    temas = {}
    for r in p.con.execute("SELECT tc.codigo_id, t.id, t.nome, t.pai_id FROM tema_codigos tc JOIN temas t ON t.id = tc.tema_id"
                           " WHERE tc.ativo = 1 AND t.situacao NOT IN ('descartado','fundido')"):
        temas.setdefault(r[0], []).append({"id": r[1], "nome": r[2], "pai_id": r[3]})
    for c in out:
        c["n_fragmentos"], c["n_documentos"], c["n_participantes"] = contagens.get(c["id"], (0, 0, 0))
        c["temas"] = temas.get(c["id"], [])
    return out


def evidencias(p: Projeto, cid: int) -> list[dict]:
    out = linhas(p.con.execute(
        "SELECT f.*, cf.id AS codificacao_id, cf.justificativa AS codificacao_justificativa, cf.criado_em AS codificado_em,"
        " cf.origem AS codificacao_origem, d.codigo AS documento_codigo, d.titulo AS documento_titulo,"
        " pa.codigo AS participante_codigo"
        " FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id JOIN documentos d ON d.id = f.documento_id"
        " LEFT JOIN participantes pa ON pa.id = d.participante_id"
        " WHERE cf.codigo_id = ? AND cf.ativo = 1 AND f.ativo = 1 ORDER BY d.codigo, f.inicio", (cid,)))
    for f in out:
        f["outros_codigos"] = [r[0] for r in p.con.execute(
            "SELECT c.nome FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id WHERE cf.fragmento_id = ?"
            " AND cf.ativo = 1 AND c.id != ? AND c.situacao = 'ativo'", (f["id"], cid))]
    return out


# ------------------------------------------------------------------ codificações
def codificar(p: Projeto, fid: int, cid: int, justificativa: str = "", origem: str = "manual") -> int:
    f = obter_fragmento(p, fid)
    c = obter_codigo(p, cid)
    if c["situacao"] != "ativo":
        raise ErroValidacao("Este código não está ativo.")
    if not f["ativo"]:
        raise ErroValidacao("Este fragmento foi removido.")
    ja = p.con.execute("SELECT id FROM codificacoes WHERE fragmento_id = ? AND codigo_id = ? AND ativo = 1", (fid, cid)).fetchone()
    if ja:
        return ja[0]
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO codificacoes(fragmento_id, codigo_id, justificativa, origem, criado_em)"
                            " VALUES (?,?,?,?,?)", (fid, cid, justificativa, origem, agora()))
        registrar(p.con, "codificacao", cur.lastrowid, "associar", fase=2,
                  depois={"fragmento_id": fid, "codigo_id": cid, "codigo": c["nome"], "documento": f["documento_codigo"],
                          "trecho": f["texto"][:300]}, justificativa=justificativa,
                  origem="ia" if origem == "ia" else "pesquisador")
        _marcar_temas_desatualizados(p, cid, "novo fragmento no código")
        _marcar_fase_em_andamento(p, 2)
        return cur.lastrowid


def codificar_selecao(p: Projeto, did: int, inicio: int, fim: int, *, codigo_ids: list[int] = (),
                      novo_codigo: dict | None = None, justificativa: str = "", texto_esperado: str | None = None) -> dict:
    with transacao(p.con):
        f = criar_fragmento(p, did, inicio, fim, texto_esperado=texto_esperado)
        ids = [int(c) for c in codigo_ids]
        if novo_codigo and (novo_codigo.get("nome") or "").strip():
            ids.append(criar_codigo(p, novo_codigo["nome"], novo_codigo.get("descricao", ""),
                                    novo_codigo.get("justificativa", ""))["id"])
        if not ids:
            raise ErroValidacao("Escolha ao menos um código ou crie um novo.")
        for cid in ids:
            codificar(p, f["id"], cid, justificativa)
    return obter_fragmento(p, f["id"])


def remover_codificacao(p: Projeto, codificacao_id: int, justificativa: str = "") -> None:
    cf = linha(p.con.execute("SELECT cf.*, c.nome FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id WHERE cf.id = ?",
                             (codificacao_id,)).fetchone())
    if not cf or not cf["ativo"]:
        raise ErroValidacao("Associação não encontrada.")
    with transacao(p.con):
        p.con.execute("UPDATE codificacoes SET ativo = 0, removido_em = ? WHERE id = ?", (agora(), codificacao_id))
        _marcar_temas_desatualizados(p, cf["codigo_id"], "fragmento removido do código")
        registrar(p.con, "codificacao", codificacao_id, "desassociar", fase=2,
                  antes={"fragmento_id": cf["fragmento_id"], "codigo_id": cf["codigo_id"], "codigo": cf["nome"], "ativo": 1},
                  depois={"ativo": 0}, justificativa=justificativa)


def matriz_codigos_documentos(p: Projeto) -> dict:
    docs = linhas(p.con.execute("SELECT d.id, d.codigo, d.titulo, pa.codigo AS participante FROM documentos d"
                                " LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE d.ativo = 1 ORDER BY d.codigo"))
    cods = listar_codigos(p)
    cel = {}
    for r in p.con.execute("SELECT cf.codigo_id, f.documento_id, COUNT(DISTINCT f.id) FROM codificacoes cf"
                           " JOIN fragmentos f ON f.id = cf.fragmento_id WHERE cf.ativo = 1 AND f.ativo = 1"
                           " GROUP BY cf.codigo_id, f.documento_id"):
        cel[(r[0], r[1])] = r[2]
    return {"documentos": docs, "codigos": cods, "celulas": {f"{k[0]}:{k[1]}": v for k, v in cel.items()}}
