"""Versões da transcrição anonimizada (a usada na análise) e consistência dos fragmentos.

Cada salvamento cria uma nova versão imutável. Ao salvar, cada fragmento
codificado é realocado no novo texto:

* ``ok``           — o trecho não foi tocado (as posições são apenas ajustadas);
* ``realocado``    — o trecho foi alterado ao redor e o mesmo texto foi
  reencontrado; o pesquisador é avisado para conferir;
* ``inconsistente``— o texto do trecho foi modificado ou removido; o
  fragmento é sinalizado e o pesquisador decide (aceitar o novo trecho,
  reposicionar ou desativar). O texto original do fragmento é preservado.
"""
from __future__ import annotations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from ..textos import MapaPosicoes, diff_linhas, estatisticas_diff, normalizar, realocar, sha256
from .projetos import ErroValidacao, Projeto

ORIGENS = {
    "anonimizacao": "Gerada pela anonimização aprovada",
    "importacao_anonimizada": "Importada como já anonimizada",
    "edicao": "Edição do pesquisador",
    "restauracao": "Restauração de versão anterior",
    "reanonimizacao": "Nova anonimização após revisão do texto protegido",
    "legado": "Importada do sistema anterior",
}


def versao_atual(p: Projeto, did: int) -> dict | None:
    return linha(p.con.execute("SELECT * FROM transcricao_versoes WHERE documento_id = ? ORDER BY numero DESC LIMIT 1",
                               (did,)).fetchone())


def obter_versao(p: Projeto, versao_id: int) -> dict:
    r = linha(p.con.execute("SELECT * FROM transcricao_versoes WHERE id = ?", (versao_id,)).fetchone())
    if not r:
        raise ErroValidacao("Versão não encontrada.")
    return r


def versoes(p: Projeto, did: int) -> list[dict]:
    out = linhas(p.con.execute(
        "SELECT id, documento_id, numero, sha256, origem, observacao, criado_em, LENGTH(texto) AS caracteres"
        " FROM transcricao_versoes WHERE documento_id = ? ORDER BY numero DESC", (did,)))
    for v in out:
        v["origem_rotulo"] = ORIGENS.get(v["origem"], v["origem"])
    return out


def _contexto_frag(texto, ini, fim):
    return texto[max(0, ini - 40):ini], texto[fim:fim + 40]


def criar_versao(p: Projeto, did: int, texto: str, origem: str, observacao: str = "") -> dict:
    """Cria nova versão e realoca os fragmentos. Retorna resumo da realocação."""
    texto = normalizar(texto)
    if not texto.strip():
        raise ErroValidacao("A transcrição não pode ficar vazia.")
    with transacao(p.con):
        anterior = versao_atual(p, did)
        if anterior and anterior["texto"] == texto:
            raise ErroValidacao("Nenhuma alteração em relação à versão atual.")
        numero = (anterior["numero"] + 1) if anterior else 1
        cur = p.con.execute(
            "INSERT INTO transcricao_versoes(documento_id, numero, texto, sha256, origem, observacao, criado_em)"
            " VALUES (?,?,?,?,?,?,?)", (did, numero, texto, sha256(texto), origem, observacao, agora()))
        vid = cur.lastrowid
        resumo = {"ok": 0, "realocado": 0, "inconsistente": 0, "fragmentos_afetados": []}
        if anterior:
            mapa = MapaPosicoes(anterior["texto"], texto)
            for f in linhas(p.con.execute("SELECT * FROM fragmentos WHERE documento_id = ? AND ativo = 1", (did,))):
                # um fragmento já inconsistente continua inconsistente até a revisão do pesquisador
                sit, ni, nf = realocar(anterior["texto"], texto, f["inicio"], f["fim"], f["texto"], mapa)
                if sit == "inconsistente":
                    sit_final = "inconsistente"
                elif f["situacao"] in ("inconsistente", "realocado") or sit == "realocado":
                    # reencontrado, mas ainda pendente de conferência pelo pesquisador
                    sit_final = "realocado"
                else:
                    sit_final = "ok"
                pre, suf = _contexto_frag(texto, ni, nf)
                p.con.execute("UPDATE fragmentos SET inicio = ?, fim = ?, prefixo = ?, sufixo = ?, situacao = ?,"
                              " atualizado_em = ? WHERE id = ?", (ni, nf, pre, suf, sit_final, agora(), f["id"]))
                chave = "ok" if sit in ("ok", "ok_deslocado") else sit
                resumo[chave] += 1
                if sit in ("realocado", "inconsistente"):
                    resumo["fragmentos_afetados"].append({"id": f["id"], "situacao": sit})
                    registrar(p.con, "fragmento", f["id"], "realocar_apos_edicao",
                              antes={"inicio": f["inicio"], "fim": f["fim"], "situacao": f["situacao"]},
                              depois={"inicio": ni, "fim": nf, "situacao": sit_final, "versao": numero},
                              origem="sistema",
                              justificativa=("Trecho reencontrado em outra posição; conferir." if sit == "realocado"
                                             else "O texto do fragmento foi alterado ou removido na edição; revisar a associação."))
        registrar(p.con, "transcricao", did, "nova_versao",
                  antes={"versao": anterior["numero"], "sha256": anterior["sha256"]} if anterior else None,
                  depois={"versao": numero, "sha256": sha256(texto), "origem": origem,
                          **(estatisticas_diff(anterior["texto"], texto) if anterior else {"caracteres": len(texto)}),
                          "realocacao": {k: v for k, v in resumo.items() if k != "fragmentos_afetados"}},
                  justificativa=observacao)
        p.con.execute("UPDATE documentos SET situacao = 'anonimizado', atualizado_em = ? WHERE id = ?", (agora(), did))
    resumo["versao_id"] = vid
    resumo["numero"] = numero
    return resumo


def salvar_edicao(p: Projeto, did: int, texto: str, observacao: str, versao_base: int | None = None) -> dict:
    atual = versao_atual(p, did)
    if not atual:
        raise ErroValidacao("O documento ainda não possui versão anonimizada para editar.")
    if versao_base is not None and int(versao_base) != atual["numero"]:
        raise ErroValidacao(f"A transcrição foi alterada em outra janela (versão atual nº {atual['numero']}). "
                            "Recarregue a página antes de salvar para não sobrescrever a outra alteração.")
    if not (observacao or "").strip():
        raise ErroValidacao("Registre uma observação sobre a alteração (ex.: 'correção de palavra mal transcrita').")
    from . import pii
    alertas = pii.verificar_texto_simples(p, normalizar(texto), somente_novos_em=atual["texto"])
    resumo = criar_versao(p, did, texto, "edicao", observacao)
    resumo["alertas_privacidade"] = alertas
    return resumo


def restaurar(p: Projeto, did: int, versao_id: int, justificativa: str) -> dict:
    v = obter_versao(p, versao_id)
    if v["documento_id"] != did:
        raise ErroValidacao("Versão não pertence a este documento.")
    if not justificativa.strip():
        raise ErroValidacao("Informe o motivo da restauração.")
    return criar_versao(p, did, v["texto"], "restauracao",
                        f"Restauração da versão nº {v['numero']}: {justificativa}")


def comparar(p: Projeto, versao_a: int, versao_b: int) -> dict:
    a, b = obter_versao(p, versao_a), obter_versao(p, versao_b)
    return {"a": a, "b": b, "linhas": diff_linhas(a["texto"], b["texto"]),
            "estatisticas": estatisticas_diff(a["texto"], b["texto"])}


def buscar(p: Projeto, did: int, termo: str, diferenciar: bool = False) -> list[dict]:
    import re
    v = versao_atual(p, did)
    if not v or not termo:
        return []
    flags = 0 if diferenciar else re.IGNORECASE
    return [{"inicio": m.start(), "fim": m.end(), "contexto": v["texto"][max(0, m.start() - 50):m.end() + 50]}
            for m in re.finditer(re.escape(termo), v["texto"], flags)]
