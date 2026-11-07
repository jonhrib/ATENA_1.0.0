"""Documentos e entrevistas: importação, versões protegidas e metadados.

Versões de cada documento (nenhuma sobrescreve outra):

1. **Original** — arquivo exatamente como importado, cifrado em ``protegido/``.
2. **Texto extraído** — conteúdo extraído do original (cofre, cifrado).
3. **Versão revisada** — correções do pesquisador antes da anonimização (cofre, cifrado, versionada).
4. **Versão anonimizada** — gerada pela anonimização aprovada; é a única usada na análise,
   na IA e nas exportações (``transcricao_versoes``, versionada).
"""
from __future__ import annotations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from ..textos import normalizar, sha256
from . import importacao, protegido
from .projetos import ErroValidacao, Projeto

TIPOS = {"entrevista": "Entrevista", "grupo_focal": "Grupo focal", "documento": "Documento",
         "nota_campo": "Nota de campo", "questionario": "Questionário aberto", "outro": "Outro"}

SITUACOES = {
    "importado": "Importado — aguardando anonimização",
    "em_anonimizacao": "Anonimização em revisão",
    "anonimizado": "Anonimizado — em uso na análise",
}


def _proximo_codigo(p: Projeto) -> str:
    n = (p.con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] or 0) + 1
    while p.con.execute("SELECT 1 FROM documentos WHERE codigo = ?", (f"DOC_{n:03d}",)).fetchone():
        n += 1
    return f"DOC_{n:03d}"


def _criar(p: Projeto, *, titulo: str, tipo: str, participante_id: int | None, nome_arquivo: str,
           dados: bytes | None, texto: str, ja_anonimizado: bool, data_coleta: str, observacoes: str,
           avisos: list[str], origem: str) -> dict:
    p.dek()  # o cofre precisa estar aberto: o conteúdo ainda pode conter dados pessoais
    if tipo not in TIPOS:
        raise ErroValidacao("Tipo de documento inválido.")
    if participante_id:
        if not p.con.execute("SELECT 1 FROM participantes WHERE id = ?", (participante_id,)).fetchone():
            raise ErroValidacao("Participante não encontrado.")
    texto = normalizar(texto)
    if not texto.strip():
        raise ErroValidacao("O documento não contém texto.")
    momento = agora()
    with transacao(p.con):
        codigo = _proximo_codigo(p)
        titulo = (titulo or "").strip() or (f"Entrevista {codigo[-3:]}" if tipo == "entrevista" else f"Documento {codigo[-3:]}")
        ext = importacao.extensao(nome_arquivo) if nome_arquivo else "txt"
        cur = p.con.execute(
            "INSERT INTO documentos(codigo, titulo, tipo, participante_id, extensao, tamanho, sha256_original, situacao,"
            " ja_anonimizado, data_coleta, observacoes, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (codigo, titulo, tipo, participante_id or None, ext, len(dados or texto.encode()), "",
             "anonimizado" if ja_anonimizado else "importado", 1 if ja_anonimizado else 0, data_coleta, observacoes,
             momento, momento))
        did = cur.lastrowid
        protegido.limpar_orfaos(p, did)
        h = ""
        if dados is not None:
            h = protegido.salvar_original(p, did, codigo, nome_arquivo, dados)
        else:
            h = sha256(texto)
        p.con.execute("UPDATE documentos SET sha256_original = ? WHERE id = ?", (h, did))
        protegido.salvar_texto(p, did, "extraido", texto, "texto extraído na importação")
        protegido.salvar_texto(p, did, "revisado", texto, "cópia inicial do texto extraído")
        registrar(p.con, "documento", did, "importar" if dados is not None else "criar_manual",
                  depois={"codigo": codigo, "titulo": titulo, "tipo": tipo, "participante_id": participante_id,
                          "extensao": ext, "tamanho": len(dados or b""), "sha256_original": h,
                          "caracteres": len(texto), "origem": origem, "avisos": avisos,
                          "ja_anonimizado": bool(ja_anonimizado)},
                  justificativa="Original preservado e cifrado na zona protegida.")
        if ja_anonimizado:
            from . import transcricoes
            transcricoes.criar_versao(p, did, texto, "importacao_anonimizada",
                                      "Texto importado declarado como já anonimizado pelo pesquisador.")
        if participante_id:
            p.con.execute("UPDATE participantes SET situacao = CASE WHEN situacao IN ('prevista','contatada','agendada','realizada')"
                          " THEN ? ELSE situacao END, atualizado_em = ? WHERE id = ?",
                          ("anonimizada" if ja_anonimizado else "transcrita", momento, participante_id))
    d = obter(p, did)
    d["avisos"] = avisos
    return d


def importar(p: Projeto, nome_arquivo: str, dados: bytes, *, titulo: str = "", tipo: str = "entrevista",
             participante_id: int | None = None, ja_anonimizado: bool = False, data_coleta: str = "",
             observacoes: str = "") -> dict:
    texto, avisos = importacao.extrair(nome_arquivo, dados)
    return _criar(p, titulo=titulo, tipo=tipo, participante_id=participante_id, nome_arquivo=nome_arquivo,
                  dados=dados, texto=texto, ja_anonimizado=ja_anonimizado, data_coleta=data_coleta,
                  observacoes=observacoes, avisos=avisos, origem="arquivo")


def criar_manual(p: Projeto, texto: str, *, titulo: str = "", tipo: str = "entrevista",
                 participante_id: int | None = None, ja_anonimizado: bool = False, data_coleta: str = "",
                 observacoes: str = "") -> dict:
    return _criar(p, titulo=titulo, tipo=tipo, participante_id=participante_id, nome_arquivo="",
                  dados=None, texto=texto, ja_anonimizado=ja_anonimizado, data_coleta=data_coleta,
                  observacoes=observacoes, avisos=[], origem="texto colado")


def obter(p: Projeto, did: int) -> dict:
    r = linha(p.con.execute(
        "SELECT d.*, pa.codigo AS participante_codigo FROM documentos d"
        " LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE d.id = ?", (did,)).fetchone())
    if not r:
        raise ErroValidacao("Documento não encontrado.")
    v = p.con.execute("SELECT id, numero, criado_em, origem FROM transcricao_versoes WHERE documento_id = ?"
                      " ORDER BY numero DESC LIMIT 1", (did,)).fetchone()
    r["versao_atual"] = linha(v)
    r["situacao_rotulo"] = SITUACOES.get(r["situacao"], r["situacao"])
    r["tipo_rotulo"] = TIPOS.get(r["tipo"], r["tipo"])
    r["versao_em_uso"] = (f"Versão anonimizada nº {v['numero']}" if v else "Nenhuma — o documento ainda não foi anonimizado")
    return r


def listar(p: Projeto, *, incluir_inativos: bool = False, situacao: str = "") -> list[dict]:
    cond = [] if incluir_inativos else ["d.ativo = 1"]
    params = []
    if situacao:
        cond.append("d.situacao = ?"); params.append(situacao)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    out = []
    for r in linhas(p.con.execute(
            "SELECT d.*, pa.codigo AS participante_codigo FROM documentos d LEFT JOIN participantes pa"
            f" ON pa.id = d.participante_id {where} ORDER BY d.codigo", params)):
        r["situacao_rotulo"] = SITUACOES.get(r["situacao"], r["situacao"])
        r["tipo_rotulo"] = TIPOS.get(r["tipo"], r["tipo"])
        v = p.con.execute("SELECT numero, LENGTH(texto) FROM transcricao_versoes WHERE documento_id = ?"
                          " ORDER BY numero DESC LIMIT 1", (r["id"],)).fetchone()
        r["versao_numero"] = v[0] if v else None
        r["caracteres"] = v[1] if v else None
        r["n_fragmentos"] = p.con.execute("SELECT COUNT(*) FROM fragmentos WHERE documento_id = ? AND ativo = 1",
                                          (r["id"],)).fetchone()[0]
        r["n_inconsistentes"] = p.con.execute(
            "SELECT COUNT(*) FROM fragmentos WHERE documento_id = ? AND ativo = 1 AND situacao = 'inconsistente'",
            (r["id"],)).fetchone()[0]
        r["n_leituras"] = p.con.execute("SELECT COUNT(*) FROM leituras WHERE documento_id = ?", (r["id"],)).fetchone()[0]
        out.append(r)
    return out


def editar_metadados(p: Projeto, did: int, dados: dict, justificativa: str = "") -> None:
    atual = obter(p, did)
    campos = {}
    for k in ("titulo", "tipo", "participante_id", "data_coleta", "observacoes"):
        if k in dados:
            v = dados[k]
            if k == "participante_id":
                v = int(v) if v not in (None, "", "0") else None
            elif isinstance(v, str):
                v = v.strip()
            if k == "tipo" and v not in TIPOS:
                raise ErroValidacao("Tipo inválido.")
            if k == "titulo" and not v:
                raise ErroValidacao("O título não pode ficar vazio.")
            if v != atual[k]:
                campos[k] = v
    if not campos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in campos)
        p.con.execute(f"UPDATE documentos SET {sets}, atualizado_em = ? WHERE id = ?", list(campos.values()) + [agora(), did])
        registrar(p.con, "documento", did, "editar", antes={k: atual[k] for k in campos}, depois=campos,
                  justificativa=justificativa)


def desativar(p: Projeto, did: int, justificativa: str) -> None:
    if not justificativa.strip():
        raise ErroValidacao("Informe uma justificativa para arquivar o documento.")
    with transacao(p.con):
        p.con.execute("UPDATE documentos SET ativo = 0, atualizado_em = ? WHERE id = ?", (agora(), did))
        registrar(p.con, "documento", did, "arquivar", antes={"ativo": 1}, depois={"ativo": 0},
                  justificativa=justificativa + " (o documento, o original e os fragmentos continuam guardados)")


def reativar(p: Projeto, did: int) -> None:
    with transacao(p.con):
        p.con.execute("UPDATE documentos SET ativo = 1, atualizado_em = ? WHERE id = ?", (agora(), did))
        registrar(p.con, "documento", did, "reativar", depois={"ativo": 1})


def salvar_revisao_protegida(p: Projeto, did: int, texto: str, observacao: str) -> int:
    """Nova versão revisada (antes da anonimização). Exige cofre aberto."""
    atual = protegido.ler_texto(p, did, "revisado")
    texto = normalizar(texto)
    if texto == atual["texto"]:
        raise ErroValidacao("Nenhuma alteração para salvar.")
    if not observacao.strip():
        raise ErroValidacao("Descreva brevemente a alteração (ex.: correção de transcrição conferida com o áudio).")
    tid = protegido.salvar_texto(p, did, "revisado", texto, observacao)
    from ..textos import estatisticas_diff
    with transacao(p.con):
        registrar(p.con, "documento", did, "revisar_texto_protegido",
                  antes={"versao_revisada": atual["numero"], "sha256": atual["sha256"]},
                  depois={"versao_revisada": atual["numero"] + 1, "sha256": sha256(texto),
                          **estatisticas_diff(atual["texto"], texto)},
                  justificativa=observacao)
    return tid


def registrar_leitura(p: Projeto, did: int, tipo: str = "abertura", observacao: str = "") -> None:
    v = p.con.execute("SELECT id FROM transcricao_versoes WHERE documento_id = ? ORDER BY numero DESC LIMIT 1",
                      (did,)).fetchone()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO leituras(documento_id, versao_id, tipo, observacao, criado_em) VALUES (?,?,?,?,?)",
                            (did, v[0] if v else None, tipo, observacao, agora()))
        if tipo != "abertura":
            registrar(p.con, "leitura", cur.lastrowid, "registrar_leitura", fase=1,
                      depois={"documento_id": did, "tipo": tipo, "observacao": observacao})


def leituras(p: Projeto, did: int | None = None) -> list[dict]:
    if did:
        return linhas(p.con.execute("SELECT l.*, d.codigo FROM leituras l JOIN documentos d ON d.id = l.documento_id"
                                    " WHERE documento_id = ? ORDER BY l.id DESC", (did,)))
    return linhas(p.con.execute("SELECT l.*, d.codigo, d.titulo FROM leituras l JOIN documentos d ON d.id = l.documento_id"
                                " ORDER BY l.id DESC LIMIT 300"))
