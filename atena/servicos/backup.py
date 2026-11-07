"""Backups manuais e automáticos, e restauração de projetos.

Um backup (``.atenabak``) é um ZIP com:

* ``projeto.sqlite`` e ``cofre.sqlite`` copiados pela API de backup do SQLite
  (cópia consistente mesmo com o programa aberto);
* ``protegido/`` — originais cifrados;
* ``manifesto.json`` — versão, data, hash SHA-256 de cada arquivo.

O cofre continua cifrado dentro do backup: sem a senha (ou o código de
recuperação) o conteúdo identificável é ilegível. A restauração nunca apaga
o projeto atual: ele é copiado para ``substituidos/`` e um backup
``pre_restauracao`` é gerado antes.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .. import NOME, VERSAO, config
from ..auditoria import registrar
from ..cofre import cofres
from ..db import agora, conectar, copiar_banco, transacao, verificar_integridade
from ..textos import sha256
from .projetos import ErroValidacao, Projeto

FORMATO = "ATENA-BACKUP-1"
_trava = threading.RLock()


def _pasta_backups(uid: str) -> Path:
    p = config.pasta_backups() / uid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nome_seguro(t: str) -> str:
    import re
    return re.sub(r"[^\w\-]+", "_", t)[:40].strip("_") or "projeto"


def criar_backup(uid: str, tipo: str = "manual", observacao: str = "") -> Path:
    if tipo not in ("manual", "automatico", "pre_restauracao"):
        raise ErroValidacao("Tipo de backup inválido.")
    with _trava:
        p = Projeto(uid)
        try:
            dados = p.dados()
            ultimo = p.con.execute("SELECT MAX(id) FROM historico").fetchone()[0] or 0
            destino = _pasta_backups(uid) / f"ATENA_{_nome_seguro(dados['nome'])}_{datetime.now():%Y%m%d-%H%M%S}_{tipo}.atenabak"
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                copiar_banco(p.con, tmp / "projeto.sqlite")
                copiar_banco(p.con_cofre, tmp / "cofre.sqlite")
                arquivos = {"projeto.sqlite": tmp / "projeto.sqlite", "cofre.sqlite": tmp / "cofre.sqlite"}
                for f in sorted(p.pasta_protegida.glob("*.bin")):
                    arquivos[f"protegido/{f.name}"] = f
                manifesto = {"formato": FORMATO, "gerador": f"{NOME} {VERSAO}", "uid": uid, "nome": dados["nome"],
                             "criado_em": agora(), "tipo": tipo, "observacao": observacao, "ultimo_historico": ultimo,
                             "arquivos": {k: sha256(v.read_bytes()) for k, v in arquivos.items()}}
                parcial = destino.with_suffix(".parcial")
                with zipfile.ZipFile(parcial, "w", zipfile.ZIP_DEFLATED) as z:
                    for k, v in arquivos.items():
                        z.write(v, k)
                    z.writestr("manifesto.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
                parcial.replace(destino)
            with transacao(p.con):
                registrar(p.con, "backup", None, f"backup_{tipo}", origem="sistema" if tipo != "manual" else "pesquisador",
                          depois={"arquivo": destino.name, "tamanho": destino.stat().st_size, "ultimo_historico": ultimo},
                          justificativa=observacao)
        finally:
            p.fechar()
        extra = config.obter("backup_pasta")
        if extra:
            try:
                alvo = Path(extra).expanduser() / "ATENA_backups" / uid
                alvo.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destino, alvo / destino.name)
            except OSError:
                pass  # a cópia extra é opcional; o backup principal já foi gravado
        _aplicar_retencao(uid)
        return destino


def _aplicar_retencao(uid: str) -> None:
    n = int(config.obter("backup_reter_ultimos") or 0)
    if n <= 0:
        return  # padrão: nenhum backup é apagado automaticamente
    autos = sorted(_pasta_backups(uid).glob("*_automatico.atenabak"))
    for velho in autos[:-n]:
        velho.unlink()


def ler_manifesto(arquivo: Path) -> dict:
    try:
        with zipfile.ZipFile(arquivo) as z:
            m = json.loads(z.read("manifesto.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ErroValidacao("Arquivo de backup inválido ou corrompido.") from exc
    if m.get("formato") != FORMATO:
        raise ErroValidacao("Este arquivo não é um backup do ATENA.")
    return m


def listar(uid: str | None = None) -> list[dict]:
    pastas = [_pasta_backups(uid)] if uid else [d for d in config.pasta_backups().iterdir() if d.is_dir()]
    out = []
    for pasta in pastas:
        for arq in pasta.glob("*.atenabak"):
            try:
                m = ler_manifesto(arq)
            except ErroValidacao:
                continue
            out.append({"arquivo": str(arq), "nome_arquivo": arq.name, "tamanho": arq.stat().st_size, **{
                k: m.get(k) for k in ("uid", "nome", "criado_em", "tipo", "observacao", "ultimo_historico")}})
    out.sort(key=lambda x: x["criado_em"] or "", reverse=True)
    return out


def _seguro(nome: str) -> bool:
    return not (nome.startswith("/") or nome.startswith("\\") or ".." in Path(nome).parts or ":" in nome)


def validar(arquivo: Path) -> dict:
    m = ler_manifesto(arquivo)
    with zipfile.ZipFile(arquivo) as z:
        nomes = set(z.namelist())
        for n in nomes:
            if not _seguro(n):
                raise ErroValidacao("Backup contém caminhos inválidos.")
        for k, h in m["arquivos"].items():
            if k not in nomes:
                raise ErroValidacao(f"Arquivo ausente no backup: {k}")
            if sha256(z.read(k)) != h:
                raise ErroValidacao(f"O arquivo {k} do backup está corrompido (hash não confere).")
        with tempfile.TemporaryDirectory() as tmp:
            for k in ("projeto.sqlite", "cofre.sqlite"):
                z.extract(k, tmp)
                con = sqlite3.connect(str(Path(tmp) / k))
                try:
                    probs = verificar_integridade(con)
                finally:
                    con.close()
                if probs:
                    raise ErroValidacao(f"Banco {k} do backup com problemas: {probs[0]}")
    return m


def restaurar(arquivo: Path, modo: str = "copia", justificativa: str = "") -> str:
    """Restaura um backup. ``modo``: 'copia' (novo projeto) ou 'substituir' (mesmo projeto). Retorna o uid."""
    arquivo = Path(arquivo)
    m = validar(arquivo)
    if modo not in ("copia", "substituir"):
        raise ErroValidacao("Modo de restauração inválido.")
    with _trava:
        if modo == "copia":
            import uuid
            uid = uuid.uuid4().hex
        else:
            uid = m["uid"]
        destino = config.pasta_projetos() / uid
        movido = None
        if modo == "substituir" and destino.exists():
            pre = criar_backup(uid, "pre_restauracao", f"antes de restaurar {arquivo.name}")
            cofres.bloquear(uid)
            movido = config.pasta_dados() / "substituidos" / f"{uid}-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.move(str(destino), str(movido))
        else:
            pre = None
        tmp = Path(tempfile.mkdtemp(dir=config.pasta_projetos(), prefix=".restaurando-"))
        try:
            with zipfile.ZipFile(arquivo) as z:
                for k in m["arquivos"]:
                    z.extract(k, tmp)
            (tmp / "protegido").mkdir(exist_ok=True)
            con = conectar(tmp / "projeto.sqlite")
            try:
                with transacao(con):
                    if modo == "copia":
                        con.execute("UPDATE projeto SET uid = ?, nome = nome || ?, atualizado_em = ? WHERE id = 1",
                                    (uid, f" (cópia restaurada em {datetime.now():%d/%m/%Y %H:%M})", agora()))
                    registrar(con, "backup", None, "restaurar_backup",
                              depois={"arquivo": arquivo.name, "backup_criado_em": m["criado_em"], "modo": modo,
                                      "uid_origem": m["uid"], "projeto_anterior_guardado_em": str(movido) if movido else None,
                                      "backup_pre_restauracao": pre.name if pre else None},
                              justificativa=justificativa or "Restauração a partir de backup.")
            finally:
                con.close()
            tmp.rename(destino)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            if movido and not destino.exists():
                shutil.move(str(movido), str(destino))  # devolve o projeto original
            raise
        return uid


def backup_completo() -> Path:
    """Um único arquivo com backups de todos os projetos (útil para trocar de computador)."""
    from .projetos import listar as listar_projetos
    destino = config.pasta_backups() / f"ATENA_completo_{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED) as z:
        for pr in listar_projetos():
            if pr.get("situacao") == "erro":
                continue
            b = criar_backup(pr["uid"], "manual", "incluído no backup completo")
            z.write(b, f"{pr['uid']}/{b.name}")
        if config.arquivo_configuracoes().is_file():
            z.write(config.arquivo_configuracoes(), "configuracoes.json")
        z.writestr("LEIA-ME.txt", "Backup completo do ATENA. Cada pasta contém o backup (.atenabak) de um projeto.\n"
                                  "Para restaurar: ATENA › Configurações › Backup › Restaurar, escolhendo cada arquivo .atenabak.\n"
                                  "A chave da API da OpenAI NÃO faz parte do backup.\n")
    return destino


def precisa_backup_automatico(uid: str) -> bool:
    if not config.obter("backup_automatico"):
        return False
    horas = float(config.obter("backup_intervalo_horas") or 24)
    autos = sorted(_pasta_backups(uid).glob("*_automatico.atenabak"))
    p = Projeto(uid)
    try:
        if p.dados()["situacao"] != "ativo":
            return False
        ultimo_hist = p.con.execute("SELECT MAX(id) FROM historico").fetchone()[0] or 0
    finally:
        p.fechar()
    todos = sorted(_pasta_backups(uid).glob("*.atenabak"), key=lambda x: x.stat().st_mtime)
    if todos:
        try:
            m = ler_manifesto(todos[-1])
            if (m.get("ultimo_historico") or 0) >= ultimo_hist - 1:
                return False  # nada mudou desde o último backup
        except ErroValidacao:
            pass
    if not autos:
        return True
    ultimo = datetime.fromtimestamp(autos[-1].stat().st_mtime)
    return datetime.now() - ultimo >= timedelta(hours=horas)


def executar_automaticos() -> list[str]:
    from .projetos import listar as listar_projetos
    feitos = []
    for pr in listar_projetos(incluir_arquivados=False):
        try:
            if precisa_backup_automatico(pr["uid"]):
                criar_backup(pr["uid"], "automatico")
                feitos.append(pr["uid"])
        except Exception as exc:  # nunca derruba o programa por causa do backup
            import logging
            logging.getLogger("atena").exception("Falha no backup automático de %s: %s", pr.get("uid"), exc)
    return feitos


def iniciar_agendador(intervalo_s: int = 900) -> threading.Thread:
    def laco():
        time.sleep(60)
        while True:
            executar_automaticos()
            time.sleep(intervalo_s)
    t = threading.Thread(target=laco, name="atena-backup", daemon=True)
    t.start()
    return t


def ultimo_backup(uid: str) -> dict | None:
    b = listar(uid)
    return b[0] if b else None
