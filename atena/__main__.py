"""Inicialização do ATENA: servidor local + abertura do navegador.

Uso:  python -m atena          (ou o executável ATENA.exe)
Opções:  --porta N   --sem-navegador
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler

from . import NOME, VERSAO, config


def configurar_logs() -> None:
    arq = config.pasta_logs() / "atena.log"
    h = RotatingFileHandler(arq, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(h)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def porta_livre(preferida: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferida))
        except OSError:
            s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def instancia_existente() -> str | None:
    """Se o ATENA já estiver aberto, devolve a URL de entrada da instância em execução."""
    arq = config.pasta_dados() / "instancia.json"
    if not arq.is_file():
        return None
    try:
        d = json.loads(arq.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{d['porta']}"
        urllib.request.urlopen(url + "/acesso", timeout=1.5)
    except urllib.error.HTTPError:
        return f"http://127.0.0.1:{d['porta']}/entrar?t={d['token']}"
    except Exception:
        return None
    return f"http://127.0.0.1:{d['porta']}/entrar?t={d['token']}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="atena", description=f"{NOME} {VERSAO}")
    ap.add_argument("--porta", type=int, default=None)
    ap.add_argument("--sem-navegador", action="store_true")
    args = ap.parse_args(argv)
    config.carregar_ambiente()
    configurar_logs()
    log = logging.getLogger("atena")

    existente = instancia_existente()
    if existente:
        print("O ATENA já está aberto. Abrindo a janela existente no navegador…")
        webbrowser.open(existente)
        return 0

    from .web.app import criar_app
    from .servicos import backup

    token = secrets.token_urlsafe(24)
    app = criar_app(token)
    porta = porta_livre(args.porta if args.porta is not None else int(config.obter("porta") or 0))
    app.config["ATENA_PORTA"] = porta
    url = f"http://127.0.0.1:{porta}/entrar?t={token}"
    (config.pasta_dados() / "instancia.json").write_text(
        json.dumps({"porta": porta, "token": token, "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(config.pasta_dados() / "instancia.json", 0o600)
    except OSError:
        pass

    backup.iniciar_agendador()
    log.info("ATENA %s iniciado na porta %s (dados em %s)", VERSAO, porta, config.pasta_dados())
    print("=" * 66)
    print(f"  {NOME} {VERSAO} — em execução neste computador")
    print(f"  Dados: {config.pasta_dados()}")
    print(f"  Endereço (somente local): http://127.0.0.1:{porta}")
    print("  Para encerrar: botão “Encerrar” na interface ou feche esta janela.")
    print("=" * 66)
    if not args.sem_navegador and config.obter("abrir_navegador"):
        threading.Thread(target=lambda: (time.sleep(1.0), webbrowser.open(url)), daemon=True).start()
    else:
        print(f"  Abra no navegador: {url}")
    try:
        try:
            from waitress import serve
            serve(app, host="127.0.0.1", port=porta, threads=8, ident=NOME)
        except ImportError:
            from werkzeug.serving import make_server
            make_server("127.0.0.1", porta, app, threaded=True).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            (config.pasta_dados() / "instancia.json").unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
