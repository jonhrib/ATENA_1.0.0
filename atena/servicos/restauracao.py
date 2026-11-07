"""Restauração de estados anteriores a partir do histórico.

A restauração nunca apaga o registro do estado atual: ela aplica o estado
anterior como **nova operação** (``restaurar``), com referência ao registro
de origem. Transcrições e relatórios têm restauração própria por versão.
"""
from __future__ import annotations

import json

from ..auditoria import registrar
from ..db import agora, linha, transacao
from . import codificacao, documentos, notas, participantes, projetos, temas
from .projetos import ErroValidacao, Projeto

CAMPOS = {
    "codigo": ("codigos", ("nome", "descricao", "justificativa", "cor")),
    "tema": ("temas", ("nome", "descricao", "conceito_central", "escopo", "limites", "relacao_pergunta", "justificativa", "cor")),
    "nota": ("notas", ("texto", "titulo")),
    "decisao": ("decisoes", ("titulo", "descricao", "justificativa")),
    "participante": ("participantes", ("metadados", "situacao", "observacoes")),
    "documento": ("documentos", ("titulo", "tipo", "participante_id", "data_coleta", "observacoes")),
    "projeto": ("projeto", ("nome", "descricao", "objetivo", "problema", "pergunta", "metodologia", "referencias", "responsavel")),
}


def restauravel(h: dict) -> str | None:
    """Descrição da restauração possível para um registro do histórico (ou None)."""
    e, op = h["entidade"], h["operacao"]
    if op == "editar" and e in CAMPOS and h.get("antes"):
        return "Restaurar os valores anteriores a esta edição"
    return {("codigo", "excluir"): "Restaurar o código excluído", ("codigo", "mesclar"): "Reativar o código mesclado",
            ("tema", "descartar"): "Reativar o tema", ("tema", "fundir"): "Reativar o tema fundido",
            ("tema", "retirar_codigo"): "Devolver o código ao tema", ("nota", "arquivar"): "Reativar a nota",
            ("documento", "arquivar"): "Reativar o documento", ("participante", "desativar"): "Reativar o participante",
            ("codificacao", "desassociar"): "Refazer a associação do fragmento ao código",
            ("fragmento", "desativar"): "Reativar o fragmento",
            ("transcricao", "nova_versao"): "Restaurar a transcrição anterior (no editor de versões)"}.get((e, op))


def restaurar(p: Projeto, hid: int, justificativa: str) -> str:
    h = linha(p.con.execute("SELECT * FROM historico WHERE id = ?", (hid,)).fetchone())
    if not h:
        raise ErroValidacao("Registro não encontrado.")
    if not justificativa.strip():
        raise ErroValidacao("Informe o motivo da restauração.")
    antes = json.loads(h["antes"]) if h["antes"] else {}
    depois = json.loads(h["depois"]) if h["depois"] else {}
    e, op, eid = h["entidade"], h["operacao"], h["entidade_id"]
    j = f"{justificativa} (restauração do registro #{hid})"
    if op == "editar" and e in CAMPOS:
        tabela, campos = CAMPOS[e]
        valores = {k: v for k, v in antes.items() if k in campos}
        if not valores:
            raise ErroValidacao("O registro não contém valores anteriores restauráveis.")
        if e == "codigo":
            codificacao.editar_codigo(p, int(eid), **valores, motivo=j)
        elif e == "tema":
            temas.editar(p, int(eid), valores, motivo=j)
        elif e == "nota":
            notas.editar(p, int(eid), valores.get("texto"), valores.get("titulo"), motivo=j)
        elif e == "decisao":
            atual = linha(p.con.execute("SELECT * FROM decisoes WHERE id = ?", (int(eid),)).fetchone())
            notas.editar_decisao(p, int(eid), valores.get("titulo", atual["titulo"]), valores.get("descricao", atual["descricao"]),
                                 valores.get("justificativa", atual["justificativa"]), motivo=j)
        elif e == "participante":
            participantes.editar(p, int(eid), metadados=json.loads(valores["metadados"]) if "metadados" in valores else None,
                                 situacao=valores.get("situacao"), observacoes=valores.get("observacoes"), justificativa=j)
        elif e == "documento":
            documentos.editar_metadados(p, int(eid), valores, justificativa=j)
        elif e == "projeto":
            projetos.atualizar(p, valores, justificativa=j)
    elif (e, op) == ("codigo", "excluir") or (e, op) == ("codigo", "mesclar"):
        codificacao.restaurar_codigo(p, int(eid), j)
    elif (e, op) in (("tema", "descartar"), ("tema", "fundir")):
        temas.reativar(p, int(eid), j)
    elif (e, op) == ("tema", "retirar_codigo"):
        temas.atribuir_codigo(p, int(eid), int(antes["codigo_id"]), j)
    elif (e, op) == ("nota", "arquivar"):
        with transacao(p.con):
            p.con.execute("UPDATE notas SET ativo = 1, atualizado_em = ? WHERE id = ?", (agora(), int(eid)))
            registrar(p.con, "nota", eid, "restaurar", antes={"ativo": 0}, depois={"ativo": 1}, justificativa=j)
    elif (e, op) == ("documento", "arquivar"):
        documentos.reativar(p, int(eid))
    elif (e, op) == ("participante", "desativar"):
        participantes.reativar(p, int(eid))
    elif (e, op) == ("codificacao", "desassociar"):
        codificacao.codificar(p, int(antes["fragmento_id"]), int(antes["codigo_id"]), j)
    elif (e, op) == ("fragmento", "desativar"):
        with transacao(p.con):
            p.con.execute("UPDATE fragmentos SET ativo = 1, situacao = 'realocado', atualizado_em = ? WHERE id = ?", (agora(), int(eid)))
            registrar(p.con, "fragmento", eid, "restaurar", antes={"ativo": 0}, depois={"ativo": 1, "situacao": "realocado"},
                      justificativa=j + " — confira a posição do fragmento e refaça as associações necessárias.")
    else:
        raise ErroValidacao("Este registro não pode ser restaurado automaticamente.")
    return "Restauração registrada como nova operação no histórico."
