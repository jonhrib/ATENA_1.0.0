"""Histórico append-only com encadeamento de hashes.

Cada registro guarda o hash do registro anterior. Qualquer alteração ou
remoção feita fora do ATENA (por exemplo, editando o arquivo SQLite com outra
ferramenta e desligando os gatilhos) quebra a cadeia e é apontada por
``verificar_cadeia``. Dentro do ATENA, gatilhos do SQLite impedem UPDATE e
DELETE na tabela ``historico``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import agora

GENESE = "0" * 64

ROTULOS_ENTIDADE = {
    "projeto": "Projeto", "termo": "Termo protegido/exceção", "participante": "Participante",
    "documento": "Documento", "transcricao": "Transcrição", "anonimizacao": "Anonimização",
    "cofre": "Cofre (mapeamento)", "fragmento": "Fragmento", "codigo": "Código",
    "codificacao": "Codificação", "tema": "Tema", "nota": "Nota/diário", "decisao": "Decisão metodológica",
    "fase": "Fase metodológica", "relatorio": "Relatório", "ia": "Inteligência artificial",
    "sugestao": "Sugestão de IA", "exportacao": "Exportação", "backup": "Backup", "leitura": "Leitura",
    "revisao_tema": "Revisão de tema", "evidencia": "Evidência", "sistema": "Sistema",
}


def _json(v) -> str | None:
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def _calcular_hash(anterior: str, campos: dict) -> str:
    base = anterior + json.dumps(campos, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def registrar(con: sqlite3.Connection, entidade: str, entidade_id, operacao: str, *,
              antes=None, depois=None, justificativa: str = "", origem: str = "pesquisador",
              fase: int | None = None) -> int:
    """Grava um registro no histórico. Deve ser chamado dentro da transação da operação."""
    ultimo = con.execute("SELECT hash FROM historico ORDER BY id DESC LIMIT 1").fetchone()
    anterior = ultimo[0] if ultimo else GENESE
    campos = {
        "momento": agora(),
        "entidade": entidade,
        "entidade_id": None if entidade_id is None else str(entidade_id),
        "operacao": operacao,
        "fase": fase,
        "antes": _json(antes),
        "depois": _json(depois),
        "justificativa": justificativa or "",
        "origem": origem,
    }
    h = _calcular_hash(anterior, campos)
    cur = con.execute(
        "INSERT INTO historico(momento, entidade, entidade_id, operacao, fase, antes, depois,"
        " justificativa, origem, hash_anterior, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (campos["momento"], entidade, campos["entidade_id"], operacao, fase, campos["antes"],
         campos["depois"], campos["justificativa"], origem, anterior, h),
    )
    return cur.lastrowid


def verificar_cadeia(con: sqlite3.Connection) -> dict:
    anterior = GENESE
    n = 0
    for r in con.execute("SELECT * FROM historico ORDER BY id"):
        campos = {k: r[k] for k in ("momento", "entidade", "entidade_id", "operacao", "fase",
                                    "antes", "depois", "justificativa", "origem")}
        if r["hash_anterior"] != anterior or _calcular_hash(anterior, campos) != r["hash"]:
            return {"integra": False, "registros": n, "quebra_em": r["id"]}
        anterior = r["hash"]
        n += 1
    return {"integra": True, "registros": n, "quebra_em": None}


def consultar(con: sqlite3.Connection, *, texto: str = "", entidade: str = "", origem: str = "",
              operacao: str = "", entidade_id: str = "", desde: str = "", ate: str = "",
              limite: int = 200, deslocamento: int = 0) -> tuple[list[dict], int]:
    cond, params = [], []
    if texto:
        cond.append("(operacao LIKE ? OR justificativa LIKE ? OR antes LIKE ? OR depois LIKE ? OR entidade LIKE ?)")
        params += [f"%{texto}%"] * 5
    if entidade:
        cond.append("entidade = ?"); params.append(entidade)
    if entidade_id:
        cond.append("entidade_id = ?"); params.append(str(entidade_id))
    if origem:
        cond.append("origem = ?"); params.append(origem)
    if operacao:
        cond.append("operacao = ?"); params.append(operacao)
    if desde:
        cond.append("momento >= ?"); params.append(desde)
    if ate:
        cond.append("momento <= ?"); params.append(ate + "T23:59:59")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    total = con.execute(f"SELECT COUNT(*) FROM historico {where}", params).fetchone()[0]
    rs = con.execute(f"SELECT * FROM historico {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                     params + [limite, deslocamento]).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        d["antes_obj"] = json.loads(d["antes"]) if d["antes"] else None
        d["depois_obj"] = json.loads(d["depois"]) if d["depois"] else None
        d["entidade_rotulo"] = ROTULOS_ENTIDADE.get(d["entidade"], d["entidade"])
        out.append(d)
    return out, total
