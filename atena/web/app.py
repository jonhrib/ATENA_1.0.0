"""Aplicação web local do ATENA (Flask), servida somente em 127.0.0.1.

Proteções da interface local:

* o servidor escuta apenas no endereço de loopback (127.0.0.1);
* um token aleatório de inicialização é exigido na primeira abertura
  (o atalho abre ``/entrar?t=<token>``) e gera um cookie de sessão
  ``HttpOnly``/``SameSite=Strict`` — outros programas ou sites não acessam a interface;
* todo POST exige token CSRF;
* o cabeçalho ``Host`` é validado (contra ataques de *DNS rebinding*);
* Content-Security-Policy restritiva: nenhum script externo é carregado.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from functools import wraps

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from .. import NOME, NOME_COMPLETO, VERSAO, config
from ..cofre import CofreBloqueado, CofreErro, cofres
from ..servicos.importacao import ErroImportacao
from ..servicos.projetos import ErroValidacao, Projeto

log = logging.getLogger("atena")


def criar_app(token_inicial: str | None = None, testes: bool = False) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="atena_sessao",
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        TEMPLATES_AUTO_RELOAD=False,
        TESTING=testes,
    )
    app.config["ATENA_TOKEN"] = token_inicial or secrets.token_urlsafe(24)
    app.config["ATENA_PORTA"] = None

    @app.before_request
    def seguranca():
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost") and not (testes and host in ("localhost", "127.0.0.1")):
            abort(403)
        if request.endpoint in ("static", "entrar", "acesso_negado"):
            return
        if not session.get("autenticado"):
            return redirect(url_for("acesso_negado"))
        if request.method == "POST":
            enviado = request.headers.get("X-CSRF") or request.form.get("_csrf") or ""
            if not hmac.compare_digest(enviado, session.get("csrf", "")):
                if request.is_json or request.headers.get("X-CSRF") is not None:
                    return jsonify({"erro": "Sessão expirada. Recarregue a página."}), 400
                flash("A sessão expirou ou o formulário é antigo. Tente novamente.", "erro")
                return redirect(request.referrer or url_for("inicio"))

    @app.after_request
    def cabecalhos(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.teardown_request
    def fechar(_exc):
        p = g.pop("p", None)
        if p is not None:
            p.fechar()

    @app.route("/entrar")
    def entrar():
        if not hmac.compare_digest(request.args.get("t", ""), app.config["ATENA_TOKEN"]):
            return redirect(url_for("acesso_negado"))
        session.clear()
        session["autenticado"] = True
        session["csrf"] = secrets.token_urlsafe(32)
        session.permanent = False
        return redirect(url_for("inicio"))

    @app.route("/acesso")
    def acesso_negado():
        return render_template("acesso.html"), 403

    @app.context_processor
    def contexto():
        p = g.get("p")
        return {"NOME": NOME, "NOME_COMPLETO": NOME_COMPLETO, "VERSAO": VERSAO, "csrf": session.get("csrf", ""),
                "proj": p.dados() if p else None, "uid": p.uid if p else None,
                "cofre_aberto": p.cofre_aberto() if p else False, "ia_ativa": bool(config.obter("ia_habilitada"))}

    @app.errorhandler(ErroValidacao)
    @app.errorhandler(ErroImportacao)
    @app.errorhandler(CofreErro)
    def erro_usuario(exc):
        if isinstance(exc, CofreBloqueado) and g.get("p") is not None and not _quer_json():
            flash(str(exc), "alerta")
            return redirect(url_for("cofre", uid=g.p.uid, proximo=request.full_path if request.method == "GET" else request.referrer))
        if _quer_json():
            return jsonify({"erro": str(exc), "cofre_bloqueado": isinstance(exc, CofreBloqueado)}), 400
        flash(str(exc), "erro")
        return redirect(request.referrer or url_for("inicio"))

    @app.errorhandler(404)
    def nao_encontrado(_e):
        return render_template("erro.html", titulo="Página não encontrada", mensagem="O endereço não existe."), 404

    @app.errorhandler(500)
    def erro_interno(e):
        log.exception("Erro interno: %s", e)
        return render_template("erro.html", titulo="Erro inesperado",
                               mensagem="Ocorreu um erro inesperado. Nenhum dado foi perdido: a operação foi desfeita. "
                                        "Detalhes foram gravados em logs/atena.log."), 500

    app.jinja_env.filters["reject_pagina"] = lambda d: {k: v for k, v in d.items() if k != "pagina"}
    from . import rotas
    rotas.registrar(app)
    return app


def _quer_json() -> bool:
    return request.is_json or request.path.startswith("/api/") or "/api/" in request.path


def com_projeto(f):
    @wraps(f)
    def interno(uid, *a, **kw):
        try:
            g.p = Projeto(uid)
        except ErroValidacao:
            abort(404)
        return f(*a, **kw)
    return interno


def json_ok(**dados):
    return jsonify({"ok": True, **dados})
