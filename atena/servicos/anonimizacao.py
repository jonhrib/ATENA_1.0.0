"""Revisão e aplicação da anonimização (pseudonimização reversível).

Fluxo:

1. ``iniciar_sessao`` — detecta candidatos na versão revisada (protegida) do documento.
2. O pesquisador decide cada item: **anonimizar** (com código), **manter** (é dado
   pessoal, mas deve permanecer — decisão registrada) ou **rejeitar** (não é dado
   pessoal). Pode corrigir trechos, adicionar itens não detectados e aplicar a
   mesma decisão a todas as ocorrências de um termo.
3. ``aplicar`` — só é permitido quando não há item pendente. Gera a versão
   anonimizada (nova versão em ``transcricao_versoes``), verifica resíduos e
   registra tudo no histórico, sem gravar valores identificáveis no banco de trabalho.

Enquanto o mapeamento existir, os dados são **pseudonimizados**, não anonimizados
de forma irreversível: com a senha do cofre é possível reidentificar os participantes.
"""
from __future__ import annotations

from collections import Counter

from ..auditoria import registrar
from ..cofre import cifrar_texto, decifrar_texto
from ..db import agora, linha, linhas, transacao
from ..textos import contexto, dobrar
from . import pii, protegido
from .projetos import ErroValidacao, Projeto

DECISOES = {"pendente": "Pendente", "anonimizar": "Anonimizar", "manter": "Manter (decisão do pesquisador)",
            "rejeitar": "Não é dado pessoal"}


def _ctx_item(sessao_id, item_id=None):
    return f"item:{sessao_id}"


def sessao_aberta(p: Projeto, did: int) -> dict | None:
    return linha(p.con_cofre.execute("SELECT * FROM sessoes WHERE documento_id = ? AND situacao = 'em_revisao'"
                                     " ORDER BY id DESC LIMIT 1", (did,)).fetchone())


def obter_sessao(p: Projeto, sid: int) -> dict:
    s = linha(p.con_cofre.execute("SELECT * FROM sessoes WHERE id = ?", (sid,)).fetchone())
    if not s:
        raise ErroValidacao("Sessão de anonimização não encontrada.")
    return s


def sessoes(p: Projeto, did: int) -> list[dict]:
    return linhas(p.con_cofre.execute("SELECT * FROM sessoes WHERE documento_id = ? ORDER BY id DESC", (did,)))


def _texto_sessao(p: Projeto, s: dict) -> str:
    return protegido.ler_texto_id(p, s["texto_id"])["texto"]


def _inserir_item(p, sid, ini, fim, trecho, categoria, fonte, confianca, codigo=None, observacao=""):
    dek = p.dek()
    cur = p.con_cofre.execute(
        "INSERT INTO itens(sessao_id, inicio, fim, trecho, categoria, fonte, confianca, codigo, observacao, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, ini, fim, cifrar_texto(dek, trecho, _ctx_item(sid)), categoria, fonte, confianca, codigo, observacao, agora()))
    return cur.lastrowid


def iniciar_sessao(p: Projeto, did: int) -> dict:
    p.dek()
    existente = sessao_aberta(p, did)
    if existente:
        return existente
    doc = p.con.execute("SELECT * FROM documentos WHERE id = ?", (did,)).fetchone()
    if not doc:
        raise ErroValidacao("Documento não encontrado.")
    texto = protegido.ler_texto(p, did, "revisado")
    cands = pii.detectar_no_projeto(p, texto["texto"])
    sid = p.con_cofre.execute("INSERT INTO sessoes(documento_id, texto_id, criado_em) VALUES (?,?,?)",
                              (did, texto["id"], agora())).lastrowid
    for c in cands:
        _inserir_item(p, sid, c.inicio, c.fim, c.trecho, c.categoria, c.fonte, c.confianca, c.codigo, c.motivo)
    with transacao(p.con):
        if doc["situacao"] == "importado":
            p.con.execute("UPDATE documentos SET situacao = 'em_anonimizacao', atualizado_em = ? WHERE id = ?", (agora(), did))
        registrar(p.con, "anonimizacao", did, "iniciar_revisao",
                  depois={"sessao": sid, "versao_revisada": texto["numero"], "candidatos": len(cands),
                          "por_categoria": dict(Counter(c.categoria for c in cands)),
                          "por_fonte": dict(Counter(c.fonte for c in cands))},
                  justificativa="Candidatos detectados automaticamente; aguardam conferência humana.")
    return obter_sessao(p, sid)


def itens(p: Projeto, sid: int, texto: str | None = None) -> list[dict]:
    dek = p.dek()
    s = obter_sessao(p, sid)
    texto = texto if texto is not None else _texto_sessao(p, s)
    out = []
    for r in p.con_cofre.execute("SELECT * FROM itens WHERE sessao_id = ? ORDER BY inicio", (sid,)):
        d = dict(r)
        d["trecho"] = decifrar_texto(dek, r["trecho"], _ctx_item(sid))
        d["contexto"] = contexto(texto, r["inicio"], r["fim"])
        d["chave"] = dobrar(d["trecho"])
        d["decisao_rotulo"] = DECISOES[d["decisao"]]
        out.append(d)
    return out


def grupos(lista: list[dict]) -> list[dict]:
    g: dict[tuple, dict] = {}
    for it in lista:
        k = (it["chave"], it["categoria"])
        if k not in g:
            g[k] = {"chave": it["chave"], "trecho": it["trecho"], "categoria": it["categoria"], "itens": [],
                    "fontes": set(), "confianca": it["confianca"], "codigo": it["codigo"]}
        g[k]["itens"].append(it)
        g[k]["fontes"].add(it["fonte"])
        if it["codigo"] and not g[k]["codigo"]:
            g[k]["codigo"] = it["codigo"]
    out = []
    for v in g.values():
        decs = {i["decisao"] for i in v["itens"]}
        v["decisao"] = decs.pop() if len(decs) == 1 else "mista"
        v["fontes"] = sorted(v["fontes"])
        v["n"] = len(v["itens"])
        out.append(v)
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    out.sort(key=lambda x: (x["decisao"] != "pendente" and x["decisao"] != "mista", ordem.get(x["confianca"], 3), x["trecho"].lower()))
    return out


def _resolver_codigo(p: Projeto, categoria: str, trecho: str, codigo: str | None, novo: bool) -> str:
    """Retorna o código de substituição, criando a identidade no cofre quando necessário."""
    categoria = (categoria or "PESSOA").upper()
    if codigo and not novo:
        ident = protegido.identidade_por_codigo(p, codigo)
        if ident:
            protegido.adicionar_variante(p, codigo, trecho)
            return codigo
        # código digitado pelo pesquisador que ainda não existe: cria com esse código
        return protegido.criar_identidade(p, categoria, trecho, [], codigo=codigo)["codigo"]
    # mesmo valor já registrado? reutiliza
    chave = dobrar(" ".join(trecho.split()))
    for i in protegido.identidades(p):
        if i["categoria"] == categoria and chave in {dobrar(v) for v in [i["valor"]] + i["variantes"]}:
            return i["codigo"]
    return protegido.criar_identidade(p, categoria, trecho, [])["codigo"]


def decidir(p: Projeto, item_ids: list[int], decisao: str, *, categoria: str | None = None,
            codigo: str | None = None, novo_codigo: bool = False, observacao: str = "") -> int:
    if decisao not in DECISOES:
        raise ErroValidacao("Decisão inválida.")
    dek = p.dek()
    if not item_ids:
        return 0
    rows = [p.con_cofre.execute("SELECT * FROM itens WHERE id = ?", (i,)).fetchone() for i in item_ids]
    rows = [r for r in rows if r]
    if not rows:
        raise ErroValidacao("Itens não encontrados.")
    sid = rows[0]["sessao_id"]
    if any(r["sessao_id"] != sid for r in rows):
        raise ErroValidacao("Itens de sessões diferentes.")
    if obter_sessao(p, sid)["situacao"] != "em_revisao":
        raise ErroValidacao("Esta sessão já foi encerrada.")
    cod_final = None
    cat = (categoria or rows[0]["categoria"]).upper()
    if decisao == "anonimizar":
        trecho = decifrar_texto(dek, rows[0]["trecho"], _ctx_item(sid))
        cod_final = _resolver_codigo(p, cat, trecho, codigo or (None if novo_codigo else rows[0]["codigo"]), novo_codigo)
        ident = protegido.identidade_por_codigo(p, cod_final)
        cat = ident["categoria"] if ident else cat
        for r in rows[1:]:
            t = decifrar_texto(dek, r["trecho"], _ctx_item(sid))
            protegido.adicionar_variante(p, cod_final, t)
    for r in rows:
        p.con_cofre.execute("UPDATE itens SET decisao = ?, categoria = ?, codigo = ?, observacao = CASE WHEN ? != '' THEN ? ELSE observacao END,"
                            " decidido_em = ? WHERE id = ?",
                            (decisao, cat, cod_final if decisao == "anonimizar" else r["codigo"], observacao, observacao,
                             agora(), r["id"]))
    with transacao(p.con):
        registrar(p.con, "anonimizacao", obter_sessao(p, sid)["documento_id"], "decidir",
                  depois={"sessao": sid, "itens": [r["id"] for r in rows], "decisao": decisao, "categoria": cat,
                          "codigo": cod_final}, justificativa=observacao)
    return len(rows)


def aprovar_sugestoes_altas(p: Projeto, sid: int) -> int:
    """Ação do pesquisador: aprova de uma vez os itens de confiança alta com código já sugerido
    ou de padrões fixos (e-mail, telefone, documentos)."""
    n = 0
    for it in itens(p, sid):
        if it["decisao"] == "pendente" and it["confianca"] == "alta" and (it["codigo"] or it["fonte"] == "padrao"):
            n += decidir(p, [it["id"]], "anonimizar", categoria=it["categoria"], codigo=it["codigo"],
                         observacao="aprovado em lote (confiança alta)")
    return n


def adicionar_manual(p: Projeto, sid: int, inicio: int, fim: int, categoria: str, *,
                     todas_ocorrencias: bool = True, observacao: str = "") -> list[int]:
    s = obter_sessao(p, sid)
    if s["situacao"] != "em_revisao":
        raise ErroValidacao("Esta sessão já foi encerrada.")
    texto = _texto_sessao(p, s)
    inicio, fim = int(inicio), int(fim)
    if not (0 <= inicio < fim <= len(texto)):
        raise ErroValidacao("Seleção inválida.")
    # apara espaços nas bordas
    while inicio < fim and texto[inicio].isspace():
        inicio += 1
    while fim > inicio and texto[fim - 1].isspace():
        fim -= 1
    trecho = texto[inicio:fim]
    if not trecho:
        raise ErroValidacao("Selecione um trecho com texto.")
    prot = pii.protegidos_do_projeto(p)
    dob = dobrar(texto)
    for t in prot:
        for a, b in pii._ocorrencias(dob, t):
            if a < fim and inicio < b:
                raise ErroValidacao(f"O trecho coincide com o termo protegido “{t}”. Remova-o da lista de termos "
                                    "protegidos (Projeto › Privacidade) se ele realmente precisar ser anonimizado.")
    existentes = [(r["inicio"], r["fim"]) for r in p.con_cofre.execute("SELECT inicio, fim FROM itens WHERE sessao_id = ?", (sid,))]
    posicoes = [(inicio, fim)]
    if todas_ocorrencias:
        posicoes = list({(a, b) for a, b in pii._ocorrencias(dob, trecho)} | {(inicio, fim)})
    novos: list[int] = []
    for a, b in sorted(posicoes):
        igual = p.con_cofre.execute("SELECT id FROM itens WHERE sessao_id = ? AND inicio = ? AND fim = ?", (sid, a, b)).fetchone()
        if igual:
            novos.append(igual[0])  # já detectado: o pesquisador decide sobre o item existente
            continue
        if any(a < y and x < b for x, y in existentes):
            # substitui itens menores sobrepostos pelo manual (ex.: corrigir "Maria" para "Maria Souza")
            sobre = [r for r in p.con_cofre.execute("SELECT id, inicio, fim FROM itens WHERE sessao_id = ?", (sid,))
                     if a < r["fim"] and r["inicio"] < b]
            if all((r["fim"] - r["inicio"]) < (b - a) for r in sobre):
                for r in sobre:
                    p.con_cofre.execute("DELETE FROM itens WHERE id = ?", (r["id"],))
            else:
                continue
        novos.append(_inserir_item(p, sid, a, b, texto[a:b], categoria.upper(), "manual", "alta", None,
                                   observacao or "adicionado manualmente pelo pesquisador"))
        existentes.append((a, b))
    with transacao(p.con):
        registrar(p.con, "anonimizacao", s["documento_id"], "adicionar_manual",
                  depois={"sessao": sid, "itens": novos, "categoria": categoria.upper(), "ocorrencias": len(novos)})
    return novos


def ajustar_trecho(p: Projeto, item_id: int, inicio: int, fim: int) -> None:
    r = p.con_cofre.execute("SELECT * FROM itens WHERE id = ?", (item_id,)).fetchone()
    if not r:
        raise ErroValidacao("Item não encontrado.")
    s = obter_sessao(p, r["sessao_id"])
    texto = _texto_sessao(p, s)
    inicio, fim = int(inicio), int(fim)
    if not (0 <= inicio < fim <= len(texto)):
        raise ErroValidacao("Posições inválidas.")
    for o in p.con_cofre.execute("SELECT id, inicio, fim FROM itens WHERE sessao_id = ? AND id != ?", (s["id"], item_id)):
        if inicio < o["fim"] and o["inicio"] < fim:
            raise ErroValidacao("O novo trecho se sobrepõe a outro item. Ajuste ou rejeite o outro item antes.")
    p.con_cofre.execute("UPDATE itens SET inicio = ?, fim = ?, trecho = ?, decisao = 'pendente', decidido_em = NULL WHERE id = ?",
                        (inicio, fim, cifrar_texto(p.dek(), texto[inicio:fim], _ctx_item(s["id"])), item_id))
    with transacao(p.con):
        registrar(p.con, "anonimizacao", s["documento_id"], "ajustar_trecho",
                  antes={"item": item_id, "inicio": r["inicio"], "fim": r["fim"]},
                  depois={"item": item_id, "inicio": inicio, "fim": fim})


def resumo(lista: list[dict]) -> dict:
    c = Counter(i["decisao"] for i in lista)
    return {"total": len(lista), "pendentes": c.get("pendente", 0), "anonimizar": c.get("anonimizar", 0),
            "manter": c.get("manter", 0), "rejeitar": c.get("rejeitar", 0)}


def previa(p: Projeto, sid: int) -> str:
    s = obter_sessao(p, sid)
    texto = _texto_sessao(p, s)
    return _substituir(texto, [i for i in itens(p, sid, texto) if i["decisao"] == "anonimizar"])


def _substituir(texto: str, aprovados: list[dict]) -> str:
    aprovados = sorted(aprovados, key=lambda i: i["inicio"])
    for a, b in zip(aprovados, aprovados[1:]):
        if b["inicio"] < a["fim"]:
            raise ErroValidacao("Há itens aprovados sobrepostos; ajuste os trechos antes de aplicar.")
    partes, pos = [], 0
    for i in aprovados:
        partes.append(texto[pos:i["inicio"]])
        partes.append(i["codigo"])
        pos = i["fim"]
    partes.append(texto[pos:])
    return "".join(partes)


def aplicar(p: Projeto, sid: int, justificativa: str = "") -> dict:
    s = obter_sessao(p, sid)
    if s["situacao"] != "em_revisao":
        raise ErroValidacao("Esta sessão já foi encerrada.")
    texto = _texto_sessao(p, s)
    lista = itens(p, sid, texto)
    res = resumo(lista)
    if res["pendentes"]:
        raise ErroValidacao(f"Ainda há {res['pendentes']} item(ns) pendente(s). Decida todos antes de aplicar.")
    aprovados = [i for i in lista if i["decisao"] == "anonimizar"]
    if any(not i["codigo"] for i in aprovados):
        raise ErroValidacao("Há item aprovado sem código de substituição.")
    # nenhum termo protegido pode ser substituído
    dob = dobrar(texto)
    for t in pii.protegidos_do_projeto(p):
        for a, b in pii._ocorrencias(dob, t):
            for i in aprovados:
                if a < i["fim"] and i["inicio"] < b:
                    raise ErroValidacao(f"O item “{i['trecho']}” coincide com o termo protegido “{t}”.")
    novo = _substituir(texto, aprovados)
    # verificação de resíduos: valores do mapeamento que continuam no texto anonimizado
    residuos = [c.dict() for c in pii.detectar(novo, protegidos=pii.protegidos_do_projeto(p),
                                                identidades=pii.identidades_do_projeto(p), usar_spacy=False)
                if c.fonte == "mapeamento"]
    mantidos = {dobrar(i["trecho"]) for i in lista if i["decisao"] == "manter"}
    residuos_inesperados = [r for r in residuos if dobrar(r["trecho"]) not in mantidos]
    from . import transcricoes
    ja_tinha = transcricoes.versao_atual(p, s["documento_id"]) is not None
    with transacao(p.con):
        resumo_versao = transcricoes.criar_versao(
            p, s["documento_id"], novo, "reanonimizacao" if ja_tinha else "anonimizacao",
            justificativa or "Anonimização revisada e aprovada pelo pesquisador.")
        registrar(p.con, "anonimizacao", s["documento_id"], "aplicar",
                  depois={"sessao": sid, **res, "por_categoria": dict(Counter(i["categoria"] for i in aprovados)),
                          "codigos_utilizados": sorted({i["codigo"] for i in aprovados}),
                          "versao_gerada": resumo_versao["numero"], "residuos_mapeamento": len(residuos_inesperados)},
                  justificativa=justificativa or "Anonimização aplicada após conferência humana.")
        doc = p.con.execute("SELECT participante_id FROM documentos WHERE id = ?", (s["documento_id"],)).fetchone()
        if doc and doc["participante_id"]:
            p.con.execute("UPDATE participantes SET situacao = 'anonimizada', atualizado_em = ? WHERE id = ? AND"
                          " situacao IN ('prevista','contatada','agendada','realizada','transcrita')",
                          (agora(), doc["participante_id"]))
    p.con_cofre.execute("UPDATE sessoes SET situacao = 'aplicada', aplicada_em = ?, versao_gerada = ? WHERE id = ?",
                        (agora(), resumo_versao["versao_id"], sid))
    return {"versao": resumo_versao, "resumo": res, "residuos": residuos_inesperados}


def cancelar(p: Projeto, sid: int, justificativa: str = "") -> None:
    s = obter_sessao(p, sid)
    if s["situacao"] != "em_revisao":
        raise ErroValidacao("Esta sessão já foi encerrada.")
    p.con_cofre.execute("UPDATE sessoes SET situacao = 'cancelada' WHERE id = ?", (sid,))
    with transacao(p.con):
        d = p.con.execute("SELECT situacao FROM documentos WHERE id = ?", (s["documento_id"],)).fetchone()
        if d and d["situacao"] == "em_anonimizacao":
            p.con.execute("UPDATE documentos SET situacao = 'importado', atualizado_em = ? WHERE id = ?",
                          (agora(), s["documento_id"]))
        registrar(p.con, "anonimizacao", s["documento_id"], "cancelar_revisao", depois={"sessao": sid},
                  justificativa=justificativa)
