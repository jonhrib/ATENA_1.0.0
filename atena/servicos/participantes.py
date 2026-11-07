"""Participantes: identificadores anônimos, metadados não identificáveis e situação da entrevista."""
from __future__ import annotations

import json

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from . import protegido
from .projetos import ErroValidacao, Projeto

SITUACOES = {
    "prevista": "Prevista", "contatada": "Contatada", "agendada": "Entrevista agendada",
    "realizada": "Entrevista realizada", "transcrita": "Transcrita", "anonimizada": "Anonimizada",
    "em_analise": "Em análise", "desistiu": "Desistiu / retirou consentimento",
}


def _limpar_metadados(metadados: dict | None) -> dict:
    return {str(k).strip(): str(v).strip() for k, v in (metadados or {}).items() if str(k).strip() and str(v).strip()}


def criar(p: Projeto, metadados: dict | None = None, situacao: str = "prevista", observacoes: str = "",
          nome_real: str = "", variantes: list[str] | None = None) -> dict:
    if situacao not in SITUACOES:
        raise ErroValidacao("Situação inválida.")
    if nome_real.strip():
        p.dek()  # exige cofre aberto antes de gravar qualquer coisa
    meta = _limpar_metadados(metadados)
    with transacao(p.con):
        n = (p.con.execute("SELECT MAX(numero) FROM participantes").fetchone()[0] or 0) + 1
        codigo = f"PARTICIPANTE_{n:03d}"
        momento = agora()
        cur = p.con.execute(
            "INSERT INTO participantes(numero, codigo, metadados, situacao, observacoes, criado_em, atualizado_em)"
            " VALUES (?,?,?,?,?,?,?)", (n, codigo, json.dumps(meta, ensure_ascii=False), situacao, observacoes, momento, momento))
        pid = cur.lastrowid
        registrar(p.con, "participante", pid, "criar",
                  depois={"codigo": codigo, "metadados": meta, "situacao": situacao, "observacoes": observacoes})
    if nome_real.strip():
        protegido.criar_identidade(p, "PARTICIPANTE", nome_real, variantes, codigo=codigo, participante_id=pid)
    return obter(p, pid)


def obter(p: Projeto, pid: int) -> dict:
    r = linha(p.con.execute("SELECT * FROM participantes WHERE id = ?", (pid,)).fetchone())
    if not r:
        raise ErroValidacao("Participante não encontrado.")
    r["metadados_obj"] = json.loads(r["metadados"] or "{}")
    r["situacao_rotulo"] = SITUACOES.get(r["situacao"], r["situacao"])
    return r


def por_codigo(p: Projeto, codigo: str) -> dict | None:
    r = p.con.execute("SELECT id FROM participantes WHERE codigo = ?", (codigo,)).fetchone()
    return obter(p, r[0]) if r else None


def listar(p: Projeto, incluir_inativos: bool = False) -> list[dict]:
    q = "SELECT * FROM participantes" + ("" if incluir_inativos else " WHERE ativo = 1") + " ORDER BY numero"
    out = []
    for r in linhas(p.con.execute(q)):
        r["metadados_obj"] = json.loads(r["metadados"] or "{}")
        r["situacao_rotulo"] = SITUACOES.get(r["situacao"], r["situacao"])
        r["n_documentos"] = p.con.execute("SELECT COUNT(*) FROM documentos WHERE participante_id = ? AND ativo = 1",
                                          (r["id"],)).fetchone()[0]
        r["n_fragmentos"] = p.con.execute(
            "SELECT COUNT(*) FROM fragmentos f JOIN documentos d ON d.id = f.documento_id"
            " WHERE d.participante_id = ? AND f.ativo = 1", (r["id"],)).fetchone()[0]
        out.append(r)
    return out


def editar(p: Projeto, pid: int, *, metadados: dict | None = None, situacao: str | None = None,
           observacoes: str | None = None, justificativa: str = "") -> None:
    atual = obter(p, pid)
    novos = {}
    if metadados is not None:
        meta = _limpar_metadados(metadados)
        if meta != atual["metadados_obj"]:
            novos["metadados"] = json.dumps(meta, ensure_ascii=False)
    if situacao is not None and situacao != atual["situacao"]:
        if situacao not in SITUACOES:
            raise ErroValidacao("Situação inválida.")
        novos["situacao"] = situacao
    if observacoes is not None and observacoes != atual["observacoes"]:
        novos["observacoes"] = observacoes
    if not novos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE participantes SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), pid])
        registrar(p.con, "participante", pid, "editar", antes={k: atual[k] for k in novos}, depois=novos,
                  justificativa=justificativa)


def desativar(p: Projeto, pid: int, justificativa: str) -> None:
    atual = obter(p, pid)
    if not justificativa.strip():
        raise ErroValidacao("Informe uma justificativa para desativar o participante.")
    with transacao(p.con):
        p.con.execute("UPDATE participantes SET ativo = 0, atualizado_em = ? WHERE id = ?", (agora(), pid))
        registrar(p.con, "participante", pid, "desativar", antes={"ativo": 1, "codigo": atual["codigo"]},
                  depois={"ativo": 0}, justificativa=justificativa)


def reativar(p: Projeto, pid: int) -> None:
    with transacao(p.con):
        p.con.execute("UPDATE participantes SET ativo = 1, atualizado_em = ? WHERE id = ?", (agora(), pid))
        registrar(p.con, "participante", pid, "reativar", depois={"ativo": 1})


def definir_identidade(p: Projeto, pid: int, nome_real: str, variantes: list[str]) -> None:
    part = obter(p, pid)
    existente = protegido.identidade_por_codigo(p, part["codigo"])
    if existente:
        protegido.atualizar_identidade(p, part["codigo"], valor=nome_real, variantes=variantes)
    else:
        protegido.criar_identidade(p, "PARTICIPANTE", nome_real, variantes, codigo=part["codigo"], participante_id=pid)
