"""Diário de pesquisa, notas de familiarização, memorandos analíticos e decisões metodológicas."""
from __future__ import annotations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from .projetos import ErroValidacao, Projeto

TIPOS = {
    "familiarizacao": "Diário de familiarização",
    "ideia": "Ideia inicial",
    "padrao": "Padrão percebido",
    "memo": "Memorando analítico",
    "diario": "Diário de pesquisa/campo",
    "observacao": "Observação",
    "reflexividade": "Reflexividade do pesquisador",
}


def criar(p: Projeto, texto: str, *, tipo: str = "observacao", titulo: str = "", fase: int | None = None,
          documento_id: int | None = None, fragmento_id: int | None = None, entidade: str = "",
          entidade_id: int | None = None) -> dict:
    if not (texto or "").strip():
        raise ErroValidacao("A nota está vazia.")
    if tipo not in TIPOS:
        raise ErroValidacao("Tipo de nota inválido.")
    if fase is None and tipo in ("familiarizacao", "ideia", "padrao"):
        fase = 1
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO notas(tipo, fase, titulo, texto, documento_id, fragmento_id, entidade, entidade_id,"
                            " criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (tipo, fase, titulo.strip(), texto.strip(), documento_id or None, fragmento_id or None,
                             entidade, entidade_id, momento, momento))
        nid = cur.lastrowid
        registrar(p.con, "nota", nid, "criar", fase=fase,
                  depois={"tipo": tipo, "titulo": titulo, "texto": texto, "documento_id": documento_id,
                          "fragmento_id": fragmento_id, "entidade": entidade, "entidade_id": entidade_id})
        if fase == 1:
            p.con.execute("UPDATE fases SET situacao = 'em_andamento', iniciada_em = COALESCE(iniciada_em, ?)"
                          " WHERE numero = 1 AND situacao = 'nao_iniciada'", (momento,))
    return obter(p, nid)


def obter(p: Projeto, nid: int) -> dict:
    n = linha(p.con.execute("SELECT n.*, d.codigo AS documento_codigo FROM notas n LEFT JOIN documentos d ON d.id = n.documento_id"
                            " WHERE n.id = ?", (nid,)).fetchone())
    if not n:
        raise ErroValidacao("Nota não encontrada.")
    n["tipo_rotulo"] = TIPOS.get(n["tipo"], n["tipo"])
    return n


def editar(p: Projeto, nid: int, texto: str, titulo: str | None = None, motivo: str = "") -> None:
    n = obter(p, nid)
    novos = {}
    if texto is not None and texto.strip() != n["texto"]:
        if not texto.strip():
            raise ErroValidacao("A nota não pode ficar vazia.")
        novos["texto"] = texto.strip()
    if titulo is not None and titulo.strip() != n["titulo"]:
        novos["titulo"] = titulo.strip()
    if not novos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE notas SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), nid])
        registrar(p.con, "nota", nid, "editar", fase=n["fase"], antes={k: n[k] for k in novos}, depois=novos,
                  justificativa=motivo)


def arquivar(p: Projeto, nid: int, motivo: str = "") -> None:
    n = obter(p, nid)
    with transacao(p.con):
        p.con.execute("UPDATE notas SET ativo = 0, atualizado_em = ? WHERE id = ?", (agora(), nid))
        registrar(p.con, "nota", nid, "arquivar", fase=n["fase"], antes={"ativo": 1, "texto": n["texto"]},
                  depois={"ativo": 0}, justificativa=motivo)


def listar(p: Projeto, *, tipo: str = "", fase: int | None = None, documento_id: int | None = None,
           texto: str = "", entidade: str = "", entidade_id: int | None = None) -> list[dict]:
    cond, params = ["n.ativo = 1"], []
    if tipo:
        cond.append("n.tipo = ?"); params.append(tipo)
    if fase:
        cond.append("n.fase = ?"); params.append(fase)
    if documento_id:
        cond.append("n.documento_id = ?"); params.append(documento_id)
    if entidade:
        cond.append("n.entidade = ?"); params.append(entidade)
    if entidade_id:
        cond.append("n.entidade_id = ?"); params.append(entidade_id)
    if texto:
        cond.append("(n.texto LIKE ? OR n.titulo LIKE ?)"); params += [f"%{texto}%"] * 2
    out = linhas(p.con.execute("SELECT n.*, d.codigo AS documento_codigo FROM notas n LEFT JOIN documentos d"
                               f" ON d.id = n.documento_id WHERE {' AND '.join(cond)} ORDER BY n.id DESC", params))
    for n in out:
        n["tipo_rotulo"] = TIPOS.get(n["tipo"], n["tipo"])
    return out


# ------------------------------------------------------------------ decisões metodológicas
def registrar_decisao(p: Projeto, titulo: str, descricao: str, justificativa: str, fase: int | None = None) -> int:
    if not titulo.strip() or not justificativa.strip():
        raise ErroValidacao("Informe o título e a justificativa da decisão.")
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute("INSERT INTO decisoes(fase, titulo, descricao, justificativa, criado_em, atualizado_em)"
                            " VALUES (?,?,?,?,?,?)", (fase or None, titulo.strip(), descricao.strip(), justificativa.strip(),
                                                      momento, momento))
        registrar(p.con, "decisao", cur.lastrowid, "registrar", fase=fase,
                  depois={"titulo": titulo, "descricao": descricao}, justificativa=justificativa)
        return cur.lastrowid


def editar_decisao(p: Projeto, did: int, titulo: str, descricao: str, justificativa: str, motivo: str = "") -> None:
    d = linha(p.con.execute("SELECT * FROM decisoes WHERE id = ?", (did,)).fetchone())
    if not d:
        raise ErroValidacao("Decisão não encontrada.")
    novos = {k: v.strip() for k, v in (("titulo", titulo), ("descricao", descricao), ("justificativa", justificativa))
             if v is not None and v.strip() != d[k]}
    if not novos:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in novos)
        p.con.execute(f"UPDATE decisoes SET {sets}, atualizado_em = ? WHERE id = ?", list(novos.values()) + [agora(), did])
        registrar(p.con, "decisao", did, "editar", fase=d["fase"], antes={k: d[k] for k in novos}, depois=novos,
                  justificativa=motivo)


def decisoes(p: Projeto) -> list[dict]:
    return linhas(p.con.execute("SELECT * FROM decisoes WHERE ativo = 1 ORDER BY id DESC"))
