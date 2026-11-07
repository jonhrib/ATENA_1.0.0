"""Armazenamento local da chave da API da OpenAI (nunca no banco de dados).

Ordem de leitura:
1. variável de ambiente ``OPENAI_API_KEY`` (se definida);
2. arquivo ``segredos.bin`` na pasta de dados, protegido com:
   - **Windows:** DPAPI (``CryptProtectData``), vinculado à sua conta do Windows —
     outro usuário ou outro computador não consegue decifrá-lo;
   - **outros sistemas:** biblioteca ``keyring`` (se instalada) ou, na falta dela,
     arquivo com permissão apenas para o seu usuário (menos seguro; prefira a
     variável de ambiente).
"""
from __future__ import annotations

import base64
import json
import os
import sys

from . import config

SERVICO = "ATENA-OpenAI"


def _arquivo():
    return config.pasta_dados() / "segredos.bin"


# ------------------------------------------------------------------ DPAPI (Windows)
def _dpapi(dados: bytes, proteger: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(dados, len(dados))
    entrada = DATA_BLOB(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    saida = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if proteger:
        ok = crypt32.CryptProtectData(ctypes.byref(entrada), "ATENA", None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(saida))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(entrada), None, None, None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(saida))
    if not ok:
        raise OSError("Falha na proteção DPAPI do Windows.")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def mecanismo() -> str:
    if sys.platform == "win32":
        return "dpapi"
    try:
        import keyring  # noqa: F401
        return "keyring"
    except ImportError:
        return "arquivo"


DESCRICAO_MECANISMO = {
    "dpapi": "Protegida pelo Windows (DPAPI), vinculada à sua conta de usuário.",
    "keyring": "Guardada no cofre de senhas do sistema operacional (keyring).",
    "arquivo": "Guardada em arquivo acessível apenas ao seu usuário (proteção limitada; prefira a variável de ambiente OPENAI_API_KEY).",
    "ambiente": "Lida da variável de ambiente OPENAI_API_KEY.",
}


def salvar_chave(chave: str) -> str:
    chave = (chave or "").strip()
    if not chave:
        raise ValueError("Informe a chave.")
    m = mecanismo()
    if m == "dpapi":
        _arquivo().write_bytes(b"DPAPI1" + _dpapi(chave.encode("utf-8"), True))
    elif m == "keyring":
        import keyring
        keyring.set_password(SERVICO, "api_key", chave)
        _arquivo().write_bytes(b"KEYRING")
    else:
        arq = _arquivo()
        arq.write_bytes(b"PLAIN1" + base64.b64encode(chave.encode("utf-8")))
        try:
            os.chmod(arq, 0o600)
        except OSError:
            pass
    return m


def obter_chave() -> tuple[str | None, str | None]:
    """Retorna (chave, mecanismo) ou (None, None)."""
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return os.environ["OPENAI_API_KEY"].strip(), "ambiente"
    arq = _arquivo()
    if not arq.is_file():
        return None, None
    dados = arq.read_bytes()
    try:
        if dados.startswith(b"DPAPI1"):
            return _dpapi(dados[6:], False).decode("utf-8"), "dpapi"
        if dados.startswith(b"KEYRING"):
            import keyring
            v = keyring.get_password(SERVICO, "api_key")
            return (v, "keyring") if v else (None, None)
        if dados.startswith(b"PLAIN1"):
            return base64.b64decode(dados[6:]).decode("utf-8"), "arquivo"
    except Exception:
        return None, None
    return None, None


def remover_chave() -> None:
    arq = _arquivo()
    if arq.is_file():
        if arq.read_bytes().startswith(b"KEYRING"):
            try:
                import keyring
                keyring.delete_password(SERVICO, "api_key")
            except Exception:
                pass
        arq.unlink()


def mascarar(chave: str | None) -> str:
    if not chave:
        return ""
    return chave[:7] + "…" + chave[-4:] if len(chave) > 14 else "…"
