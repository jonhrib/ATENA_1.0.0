"""Projetos de pesquisa: criação, abertura, edição, arquivamento e listas de termos."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .. import config, cofre as cofre_mod
from ..auditoria import registrar
from ..cofre import cofres
from ..db import agora, conectar, inicializar_cofre, inicializar_projeto, linha, linhas, transacao


class ErroValidacao(ValueError):
    """Erro de entrada do usuário (mensagem pronta para exibição)."""


CAMPOS_EDITAVEIS = ("nome", "descricao", "objetivo", "problema", "pergunta", "metodologia",
                    "referencias", "responsavel")

CAMPOS_PARTICIPANTE_PADRAO = ["Instituição", "Curso", "Período/etapa", "Vínculo", "Grupo/perfil"]


class Projeto:
    """Acesso a um projeto: bancos de trabalho e protegido, e pasta de arquivos."""

    def __init__(self, uid: str):
        if not re.fullmatch(r"[0-9a-f]{32}", uid or ""):
            raise ErroValidacao("Identificador de projeto inválido.")
        self.uid = uid
        self.pasta = config.pasta_projetos() / uid
        if not (self.pasta / "projeto.sqlite").is_file():
            raise ErroValidacao("Projeto não encontrado.")
        self.con = conectar(self.pasta / "projeto.sqlite")
        inicializar_projeto(self.con)
        self.con_cofre = conectar(self.pasta / "cofre.sqlite")
        inicializar_cofre(self.con_cofre)
        (self.pasta / "protegido").mkdir(exist_ok=True)

    # -- conveniência
    @property
    def pasta_protegida(self) -> Path:
        return self.pasta / "protegido"

    def dek(self) -> bytes:
        return cofres.obter(self.uid)

    def cofre_aberto(self) -> bool:
        return cofres.aberto(self.uid)

    def dados(self) -> dict:
        return linha(self.con.execute("SELECT * FROM projeto WHERE id = 1").fetchone())

    def fechar(self):
        for c in (self.con, self.con_cofre):
            try:
                c.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fechar()


def listar(incluir_arquivados: bool = True) -> list[dict]:
    out = []
    for pasta in sorted(config.pasta_projetos().iterdir()):
        arq = pasta / "projeto.sqlite"
        if not arq.is_file():
            continue
        try:
            con = conectar(arq)
            try:
                p = linha(con.execute("SELECT * FROM projeto WHERE id = 1").fetchone())
                if not p:
                    continue
                p["n_documentos"] = con.execute("SELECT COUNT(*) FROM documentos WHERE ativo=1").fetchone()[0]
                p["n_participantes"] = con.execute("SELECT COUNT(*) FROM participantes WHERE ativo=1").fetchone()[0]
                p["n_codigos"] = con.execute("SELECT COUNT(*) FROM codigos WHERE situacao='ativo'").fetchone()[0]
                p["n_temas"] = con.execute(
                    "SELECT COUNT(*) FROM temas WHERE situacao NOT IN ('descartado','fundido')").fetchone()[0]
                p["ultima_atividade"] = (con.execute("SELECT MAX(momento) FROM historico").fetchone()[0]
                                         or p["atualizado_em"])
            finally:
                con.close()
        except Exception as exc:  # banco ilegível: aparece na lista como problema
            p = {"uid": pasta.name, "nome": f"(projeto ilegível: {exc})", "situacao": "erro"}
        if incluir_arquivados or p.get("situacao") == "ativo":
            out.append(p)
    out.sort(key=lambda x: x.get("ultima_atividade") or "", reverse=True)
    return out


def criar(dados: dict, senha_cofre: str, *, termos_protegidos: list[str] | None = None,
          campos_participante: list[str] | None = None) -> tuple[str, str]:
    """Cria o projeto e o seu cofre. Retorna (uid, código de recuperação do cofre)."""
    nome = (dados.get("nome") or "").strip()
    if not nome:
        raise ErroValidacao("Informe o nome do projeto.")
    cofre_mod.validar_senha(senha_cofre)
    uid = uuid.uuid4().hex
    pasta = config.pasta_projetos() / uid
    pasta.mkdir(parents=True)
    (pasta / "protegido").mkdir()
    con = conectar(pasta / "projeto.sqlite")
    con_c = conectar(pasta / "cofre.sqlite")
    try:
        inicializar_projeto(con)
        inicializar_cofre(con_c)
        dek, codigo = cofre_mod.criar(con_c, senha_cofre)
        momento = agora()
        campos = campos_participante if campos_participante is not None else CAMPOS_PARTICIPANTE_PADRAO
        with transacao(con):
            valores = {k: (dados.get(k) or "").strip() for k in CAMPOS_EDITAVEIS}
            valores["nome"] = nome
            if not valores["metodologia"]:
                valores["metodologia"] = "Análise temática indutiva (Braun e Clarke, 2006)"
            con.execute(
                "INSERT INTO projeto(id, uid, nome, descricao, objetivo, problema, pergunta, metodologia,"
                " referencias, responsavel, situacao, campos_participante, criado_em, atualizado_em)"
                " VALUES (1,?,?,?,?,?,?,?,?,?, 'ativo', ?, ?, ?)",
                (uid, valores["nome"], valores["descricao"], valores["objetivo"], valores["problema"],
                 valores["pergunta"], valores["metodologia"], valores["referencias"], valores["responsavel"],
                 json.dumps(campos, ensure_ascii=False), momento, momento))
            registrar(con, "projeto", uid, "criar", depois=valores)
            registrar(con, "cofre", uid, "criar_cofre",
                      justificativa="Cofre criptografado criado (AES-256-GCM, chave protegida por senha e código de recuperação).",
                      origem="sistema")
            for t in termos_protegidos or []:
                _inserir_termo(con, "protegido", t, "")
        cofres.guardar(uid, dek)
    except BaseException:
        con.close(); con_c.close()
        # projeto incompleto: move para 'substituidos' em vez de apagar
        destino = config.pasta_dados() / "substituidos" / f"{uid}-falha-criacao"
        try:
            pasta.rename(destino)
        except OSError:
            pass
        raise
    con.close(); con_c.close()
    return uid, codigo


def atualizar(p: Projeto, dados: dict, justificativa: str = "") -> None:
    antes = p.dados()
    novos = {k: (dados.get(k) if dados.get(k) is not None else antes[k]) for k in CAMPOS_EDITAVEIS}
    novos = {k: (v or "").strip() for k, v in novos.items()}
    if not novos["nome"]:
        raise ErroValidacao("O nome do projeto não pode ficar vazio.")
    mudou = {k: v for k, v in novos.items() if v != antes[k]}
    if not mudou:
        return
    with transacao(p.con):
        sets = ", ".join(f"{k} = ?" for k in mudou)
        p.con.execute(f"UPDATE projeto SET {sets}, atualizado_em = ? WHERE id = 1",
                      list(mudou.values()) + [agora()])
        registrar(p.con, "projeto", p.uid, "editar", antes={k: antes[k] for k in mudou}, depois=mudou,
                  justificativa=justificativa)


def definir_campos_participante(p: Projeto, campos: list[str]) -> None:
    campos = [c.strip() for c in campos if c.strip()]
    antes = json.loads(p.dados()["campos_participante"] or "[]")
    with transacao(p.con):
        p.con.execute("UPDATE projeto SET campos_participante = ?, atualizado_em = ? WHERE id = 1",
                      (json.dumps(campos, ensure_ascii=False), agora()))
        registrar(p.con, "projeto", p.uid, "editar_campos_participante", antes=antes, depois=campos)


def alterar_situacao(p: Projeto, situacao: str, justificativa: str = "") -> None:
    if situacao not in ("ativo", "arquivado"):
        raise ErroValidacao("Situação inválida.")
    antes = p.dados()["situacao"]
    if antes == situacao:
        return
    with transacao(p.con):
        p.con.execute("UPDATE projeto SET situacao = ?, atualizado_em = ? WHERE id = 1", (situacao, agora()))
        registrar(p.con, "projeto", p.uid, "arquivar" if situacao == "arquivado" else "reativar",
                  antes={"situacao": antes}, depois={"situacao": situacao}, justificativa=justificativa)


# ------------------------------------------------------------------ termos
def _inserir_termo(con, tipo, termo, observacao):
    termo = " ".join((termo or "").split())
    if not termo:
        return None
    r = con.execute("SELECT id, ativo FROM termos WHERE tipo = ? AND termo = ?", (tipo, termo)).fetchone()
    if r:
        if not r["ativo"]:
            con.execute("UPDATE termos SET ativo = 1 WHERE id = ?", (r["id"],))
            registrar(con, "termo", r["id"], "reativar", depois={"tipo": tipo, "termo": termo})
        return r["id"]
    cur = con.execute("INSERT INTO termos(tipo, termo, observacao, criado_em) VALUES (?,?,?,?)",
                      (tipo, termo, observacao or "", agora()))
    registrar(con, "termo", cur.lastrowid, "criar", depois={"tipo": tipo, "termo": termo, "observacao": observacao})
    return cur.lastrowid


def adicionar_termos(p: Projeto, tipo: str, termos: list[str], observacao: str = "") -> int:
    if tipo not in ("protegido", "excecao"):
        raise ErroValidacao("Tipo de termo inválido.")
    n = 0
    with transacao(p.con):
        for t in termos:
            if _inserir_termo(p.con, tipo, t, observacao):
                n += 1
    return n


def remover_termo(p: Projeto, termo_id: int, justificativa: str = "") -> None:
    r = linha(p.con.execute("SELECT * FROM termos WHERE id = ?", (termo_id,)).fetchone())
    if not r:
        raise ErroValidacao("Termo não encontrado.")
    with transacao(p.con):
        p.con.execute("UPDATE termos SET ativo = 0 WHERE id = ?", (termo_id,))
        registrar(p.con, "termo", termo_id, "desativar", antes=r, justificativa=justificativa)


def listar_termos(p: Projeto, tipo: str) -> list[dict]:
    return linhas(p.con.execute("SELECT * FROM termos WHERE tipo = ? AND ativo = 1 ORDER BY termo COLLATE NOCASE",
                                (tipo,)))


def listas_predefinidas() -> dict[str, list[str]]:
    """Listas de termos protegidos que acompanham o programa (pasta recursos)."""
    out = {}
    pasta = config.pasta_recursos() / "listas_protegidas"
    if pasta.is_dir():
        for arq in sorted(pasta.glob("*.txt")):
            itens = [l.split("#", 1)[0].strip() for l in arq.read_text(encoding="utf-8").splitlines()]
            out[arq.stem] = [i for i in itens if i]
    return out
