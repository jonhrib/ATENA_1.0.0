"""Painel de acompanhamento, pendências, alertas e verificação de integridade."""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import config
from ..auditoria import ROTULOS_ENTIDADE, verificar_cadeia
from ..db import linhas, verificar_integridade
from ..textos import sha256
from . import backup, fases
from .projetos import Projeto


def _q(p, sql, *a):
    return p.con.execute(sql, a).fetchone()[0]


def resumo(p: Projeto) -> dict:
    return {
        "participantes": _q(p, "SELECT COUNT(*) FROM participantes WHERE ativo = 1"),
        "documentos": _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1"),
        "entrevistas": _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND tipo IN ('entrevista','grupo_focal')"),
        "anonimizados": _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND situacao = 'anonimizado'"),
        "pendentes_anonimizacao": _q(p, "SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND situacao != 'anonimizado'"),
        "codigos": _q(p, "SELECT COUNT(*) FROM codigos WHERE situacao = 'ativo'"),
        "fragmentos": _q(p, "SELECT COUNT(*) FROM fragmentos WHERE ativo = 1"),
        "codificacoes": _q(p, "SELECT COUNT(*) FROM codificacoes cf JOIN fragmentos f ON f.id = cf.fragmento_id"
                              " WHERE cf.ativo = 1 AND f.ativo = 1"),
        "temas_construcao": _q(p, "SELECT COUNT(*) FROM temas WHERE situacao IN ('candidato','em_revisao')"),
        "temas_revisados": _q(p, "SELECT COUNT(*) FROM temas WHERE situacao = 'revisado'"),
        "temas_definidos": _q(p, "SELECT COUNT(*) FROM temas WHERE situacao = 'definido'"),
        "notas": _q(p, "SELECT COUNT(*) FROM notas WHERE ativo = 1"),
        "relatorios": _q(p, "SELECT COUNT(*) FROM relatorios WHERE ativo = 1"),
        "sugestoes_pendentes": _q(p, "SELECT COUNT(*) FROM sugestoes WHERE situacao = 'pendente'"),
    }


def pendencias(p: Projeto) -> list[dict]:
    """Tarefas pendentes com link direto. Cada item: nivel, texto, rota, args."""
    out = []
    uid = p.uid
    for d in linhas(p.con.execute("SELECT id, codigo, titulo, situacao FROM documentos WHERE ativo = 1 AND situacao != 'anonimizado'"
                                  " ORDER BY codigo")):
        out.append({"nivel": "alerta", "texto": f"{d['codigo']} — {d['titulo']}: " +
                    ("revisão de anonimização em andamento" if d["situacao"] == "em_anonimizacao" else "aguardando anonimização"),
                    "rota": "anonimizacao", "args": {"uid": uid, "did": d["id"]}})
    n = _q(p, "SELECT COUNT(*) FROM fragmentos WHERE ativo = 1 AND situacao = 'inconsistente'")
    if n:
        out.append({"nivel": "erro", "texto": f"{n} fragmento(s) inconsistente(s) após edição de transcrição — revisar associação.",
                    "rota": "fragmentos_pendentes", "args": {"uid": uid}})
    n = _q(p, "SELECT COUNT(*) FROM fragmentos WHERE ativo = 1 AND situacao = 'realocado'")
    if n:
        out.append({"nivel": "alerta", "texto": f"{n} fragmento(s) realocado(s) automaticamente — conferir.",
                    "rota": "fragmentos_pendentes", "args": {"uid": uid}})
    n = _q(p, "SELECT COUNT(*) FROM sugestoes WHERE situacao = 'pendente'")
    if n:
        out.append({"nivel": "info", "texto": f"{n} sugestão(ões) de IA aguardando sua decisão.", "rota": "ia_painel", "args": {"uid": uid}})
    sem = _q(p, "SELECT COUNT(*) FROM documentos d WHERE d.ativo = 1 AND d.situacao = 'anonimizado' AND NOT EXISTS"
                " (SELECT 1 FROM fragmentos f JOIN codificacoes cf ON cf.fragmento_id = f.id WHERE f.documento_id = d.id"
                " AND f.ativo = 1 AND cf.ativo = 1)")
    if sem and _q(p, "SELECT COUNT(*) FROM codigos WHERE situacao = 'ativo'"):
        out.append({"nivel": "info", "texto": f"{sem} entrevista(s) anonimizada(s) ainda sem nenhum fragmento codificado.",
                    "rota": "documentos", "args": {"uid": uid}})
    from .temas import codigos_sem_tema
    n = len(codigos_sem_tema(p))
    if n and _q(p, "SELECT COUNT(*) FROM temas WHERE situacao NOT IN ('descartado','fundido')"):
        out.append({"nivel": "info", "texto": f"{n} código(s) ainda não associado(s) a nenhum tema.", "rota": "mapa", "args": {"uid": uid}})
    n = _q(p, "SELECT COUNT(*) FROM codigos c WHERE c.situacao = 'ativo' AND NOT EXISTS (SELECT 1 FROM codificacoes cf"
              " JOIN fragmentos f ON f.id = cf.fragmento_id WHERE cf.codigo_id = c.id AND cf.ativo = 1 AND f.ativo = 1)")
    if n:
        out.append({"nivel": "info", "texto": f"{n} código(s) sem nenhum fragmento associado.", "rota": "codigos", "args": {"uid": uid}})
    n = _q(p, "SELECT COUNT(*) FROM temas WHERE situacao = 'definido' AND definicao_desatualizada = 1")
    if n:
        out.append({"nivel": "alerta", "texto": f"{n} tema(s) alterado(s) depois da definição confirmada — reconfirmar.",
                    "rota": "definicao", "args": {"uid": uid}})
    n = _q(p, "SELECT COUNT(*) FROM temas WHERE situacao IN ('candidato','em_revisao')")
    if n:
        out.append({"nivel": "info", "texto": f"{n} tema(s) aguardando revisão (fase 4).", "rota": "revisao", "args": {"uid": uid}})
    from ..cofre import recuperacao_confirmada
    if not recuperacao_confirmada(p.con_cofre):
        out.append({"nivel": "erro", "texto": "Você ainda não confirmou que guardou o código de recuperação do cofre.",
                    "rota": "cofre", "args": {"uid": uid}})
    ub = backup.ultimo_backup(uid)
    limite = timedelta(hours=float(config.obter("backup_intervalo_horas") or 24) * 2)
    if not ub:
        out.append({"nivel": "alerta", "texto": "Nenhum backup deste projeto foi feito ainda.", "rota": "backup_projeto", "args": {"uid": uid}})
    elif datetime.now().astimezone() - datetime.fromisoformat(ub["criado_em"]) > limite:
        out.append({"nivel": "alerta", "texto": f"Último backup em {ub['criado_em'][:16].replace('T', ' ')}.",
                    "rota": "backup_projeto", "args": {"uid": uid}})
    return out


def atividades(p: Projeto, n: int = 12) -> list[dict]:
    out = linhas(p.con.execute("SELECT * FROM historico WHERE entidade NOT IN ('leitura') ORDER BY id DESC LIMIT ?", (n,)))
    for h in out:
        h["entidade_rotulo"] = ROTULOS_ENTIDADE.get(h["entidade"], h["entidade"])
    return out


def integridade(p: Projeto) -> dict:
    problemas = []
    problemas += [f"[banco de trabalho] {x}" for x in verificar_integridade(p.con)]
    problemas += [f"[cofre] {x}" for x in verificar_integridade(p.con_cofre)]
    cadeia = verificar_cadeia(p.con)
    if not cadeia["integra"]:
        problemas.append(f"A cadeia de hashes do histórico está quebrada no registro {cadeia['quebra_em']} — "
                         "o banco pode ter sido modificado fora do ATENA.")
    for v in linhas(p.con.execute("SELECT id, documento_id, numero, sha256, texto FROM transcricao_versoes")):
        if sha256(v["texto"]) != v["sha256"]:
            problemas.append(f"Versão {v['numero']} do documento {v['documento_id']} não confere com o hash registrado.")
    for r in linhas(p.con_cofre.execute("SELECT documento_id, arquivo FROM originais")):
        if not (p.pasta_protegida / r["arquivo"]).is_file():
            problemas.append(f"Arquivo original cifrado ausente: documento {r['documento_id']} ({r['arquivo']}).")
    for f in linhas(p.con.execute("SELECT f.id, f.inicio, f.fim, f.texto, f.situacao, f.documento_id FROM fragmentos f WHERE f.ativo = 1")):
        v = p.con.execute("SELECT texto FROM transcricao_versoes WHERE documento_id = ? ORDER BY numero DESC LIMIT 1",
                          (f["documento_id"],)).fetchone()
        if v and f["situacao"] == "ok" and v[0][f["inicio"]:f["fim"]] != f["texto"]:
            problemas.append(f"Fragmento {f['id']} não corresponde ao texto da versão atual (marcado como ok).")
    return {"ok": not problemas, "problemas": problemas, "cadeia": cadeia, "verificado_em": datetime.now().isoformat(timespec="seconds")}


def painel(p: Projeto) -> dict:
    return {"resumo": resumo(p), "fases": fases.listar(p), "pendencias": pendencias(p), "atividades": atividades(p),
            "ultimo_backup": backup.ultimo_backup(p.uid)}
