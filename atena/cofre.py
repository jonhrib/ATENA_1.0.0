"""Cofre criptografado de cada projeto (zona protegida).

Esquema de chaves (criptografia de envelope):

* Uma chave de dados (DEK) aleatória de 256 bits cifra todo o conteúdo
  identificável com AES-256-GCM.
* A DEK nunca é gravada em texto puro. Ela é guardada duas vezes, "embrulhada":
  - com uma chave derivada da **senha do cofre** (scrypt, sal aleatório);
  - com uma chave derivada do **código de recuperação** exibido uma única vez
    na criação do cofre.
* Sem a senha ou o código de recuperação, o conteúdo protegido é ilegível —
  inclusive para quem copiar a pasta do projeto ou um backup.

A DEK aberta fica apenas na memória do programa e é descartada após o tempo
de inatividade configurado (padrão: 20 minutos) ou quando o pesquisador
bloqueia o cofre.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .db import agora


class CofreErro(RuntimeError):
    pass


class CofreBloqueado(CofreErro):
    pass


class SenhaIncorreta(CofreErro):
    pass


SENHA_MINIMA = 8


def _params_kdf() -> dict:
    # Os testes automatizados usam parâmetros mais leves para rodar rápido.
    if os.environ.get("ATENA_KDF_RAPIDO") == "1":
        return {"n": 2 ** 12, "r": 8, "p": 1}
    return {"n": 2 ** 15, "r": 8, "p": 1}


def _derivar(segredo: str, sal: bytes, params: dict) -> bytes:
    kdf = Scrypt(salt=sal, length=32, n=params["n"], r=params["r"], p=params["p"])
    return kdf.derive(segredo.encode("utf-8"))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def cifrar_bytes(chave: bytes, dados: bytes, contexto: str = "") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(chave).encrypt(nonce, dados, contexto.encode("utf-8"))


def decifrar_bytes(chave: bytes, dados: bytes, contexto: str = "") -> bytes:
    try:
        return AESGCM(chave).decrypt(dados[:12], dados[12:], contexto.encode("utf-8"))
    except InvalidTag as exc:
        raise CofreErro("Conteúdo protegido corrompido ou chave incorreta.") from exc


def cifrar_texto(chave: bytes, texto: str, contexto: str = "") -> bytes:
    return cifrar_bytes(chave, (texto or "").encode("utf-8"), contexto)


def decifrar_texto(chave: bytes, dados: bytes | None, contexto: str = "") -> str:
    if dados is None:
        return ""
    return decifrar_bytes(chave, bytes(dados), contexto).decode("utf-8")


def gerar_codigo_recuperacao() -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    bruto = "".join(secrets.choice(alfabeto) for _ in range(24))
    return "-".join(bruto[i:i + 4] for i in range(0, 24, 4))


def _normalizar_codigo(codigo: str) -> str:
    return "".join(c for c in codigo.upper() if c.isalnum())


def _embrulhar(dek: bytes, segredo: str) -> dict:
    sal = os.urandom(16)
    params = _params_kdf()
    kek = _derivar(segredo, sal, params)
    return {"sal": _b64(sal), "params": params, "dek": _b64(cifrar_bytes(kek, dek, "dek"))}


def _desembrulhar(pacote: dict, segredo: str) -> bytes:
    kek = _derivar(segredo, _ub64(pacote["sal"]), pacote["params"])
    try:
        return AESGCM(kek).decrypt(_ub64(pacote["dek"])[:12], _ub64(pacote["dek"])[12:], b"dek")
    except InvalidTag as exc:
        raise SenhaIncorreta("Senha ou código de recuperação incorreto.") from exc


def _meta(con, chave):
    r = con.execute("SELECT valor FROM cofre_meta WHERE chave = ?", (chave,)).fetchone()
    return r[0] if r else None


def _set_meta(con, chave, valor):
    con.execute("INSERT INTO cofre_meta(chave, valor) VALUES (?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, valor))


def validar_senha(senha: str) -> None:
    if len(senha or "") < SENHA_MINIMA:
        raise CofreErro(f"A senha do cofre deve ter pelo menos {SENHA_MINIMA} caracteres.")


def cofre_existe(con) -> bool:
    return _meta(con, "embrulho_senha") is not None


def criar(con, senha: str) -> tuple[bytes, str]:
    """Cria o cofre. Retorna (DEK, código de recuperação)."""
    validar_senha(senha)
    if cofre_existe(con):
        raise CofreErro("Este projeto já possui um cofre.")
    dek = AESGCM.generate_key(bit_length=256)
    codigo = gerar_codigo_recuperacao()
    con.execute("BEGIN IMMEDIATE")
    try:
        _set_meta(con, "versao", "1")
        _set_meta(con, "criado_em", agora())
        _set_meta(con, "embrulho_senha", json.dumps(_embrulhar(dek, senha)))
        _set_meta(con, "embrulho_recuperacao", json.dumps(_embrulhar(dek, _normalizar_codigo(codigo))))
        _set_meta(con, "impressao_dek", hashlib.sha256(b"atena-dek" + dek).hexdigest()[:16])
        _set_meta(con, "recuperacao_confirmada", "0")
        registrar_acesso(con, "criacao", "cofre criado", em_transacao=True)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return dek, codigo


def abrir(con, senha: str) -> bytes:
    if not cofre_existe(con):
        raise CofreErro("Este projeto ainda não possui cofre.")
    dek = _desembrulhar(json.loads(_meta(con, "embrulho_senha")), senha)
    registrar_acesso(con, "desbloqueio", "senha")
    return dek


def abrir_com_recuperacao(con, codigo: str, nova_senha: str) -> bytes:
    validar_senha(nova_senha)
    dek = _desembrulhar(json.loads(_meta(con, "embrulho_recuperacao")), _normalizar_codigo(codigo))
    _set_meta(con, "embrulho_senha", json.dumps(_embrulhar(dek, nova_senha)))
    registrar_acesso(con, "recuperacao", "senha redefinida com o código de recuperação")
    return dek


def trocar_senha(con, senha_atual: str, nova_senha: str) -> None:
    validar_senha(nova_senha)
    dek = _desembrulhar(json.loads(_meta(con, "embrulho_senha")), senha_atual)
    _set_meta(con, "embrulho_senha", json.dumps(_embrulhar(dek, nova_senha)))
    registrar_acesso(con, "troca_senha", "")


def novo_codigo_recuperacao(con, dek: bytes) -> str:
    codigo = gerar_codigo_recuperacao()
    _set_meta(con, "embrulho_recuperacao", json.dumps(_embrulhar(dek, _normalizar_codigo(codigo))))
    _set_meta(con, "recuperacao_confirmada", "0")
    registrar_acesso(con, "novo_codigo_recuperacao", "")
    return codigo


def confirmar_recuperacao(con) -> None:
    _set_meta(con, "recuperacao_confirmada", "1")


def recuperacao_confirmada(con) -> bool:
    return _meta(con, "recuperacao_confirmada") == "1"


def registrar_acesso(con, operacao: str, detalhe: str = "", em_transacao: bool = False) -> None:
    con.execute("INSERT INTO acessos(momento, operacao, detalhe) VALUES (?, ?, ?)",
                (agora(), operacao, detalhe))


# ------------------------------------------------------------------ sessão
class GerenciadorCofres:
    """Mantém em memória as chaves dos cofres desbloqueados, com expiração."""

    def __init__(self):
        self._chaves: dict[str, tuple[bytes, float]] = {}
        self._trava = threading.Lock()

    def _limite(self) -> float:
        from . import config
        return float(config.obter("cofre_bloqueio_minutos") or 20) * 60

    def guardar(self, uid: str, dek: bytes) -> None:
        with self._trava:
            self._chaves[uid] = (dek, time.monotonic())

    def obter(self, uid: str) -> bytes:
        with self._trava:
            item = self._chaves.get(uid)
            if item is None:
                raise CofreBloqueado("O cofre deste projeto está bloqueado. Informe a senha para continuar.")
            dek, ultimo = item
            if time.monotonic() - ultimo > self._limite():
                del self._chaves[uid]
                raise CofreBloqueado("O cofre foi bloqueado por inatividade. Informe a senha novamente.")
            self._chaves[uid] = (dek, time.monotonic())
            return dek

    def aberto(self, uid: str) -> bool:
        try:
            self.obter(uid)
            return True
        except CofreBloqueado:
            return False

    def bloquear(self, uid: str | None = None) -> None:
        with self._trava:
            if uid is None:
                self._chaves.clear()
            else:
                self._chaves.pop(uid, None)


cofres = GerenciadorCofres()
