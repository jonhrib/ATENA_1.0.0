"""Rotas da interface do ATENA."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
from pathlib import Path

from flask import abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for

from .. import config, segredos
from ..auditoria import ROTULOS_ENTIDADE, consultar, verificar_cadeia
from ..cofre import CofreErro, SenhaIncorreta, cofres
from .. import cofre as cofre_mod
from ..db import linhas
from ..servicos import (anonimizacao as anon, backup, codificacao as cod, demo, documentos as docs, exportacao, fases, ia,
                        legado, notas, painel, participantes as parts, projetos, protegido, relatorios as rel, restauracao,
                        temas as tm, transcricoes as tr, visualizacoes as vz)
from ..servicos.projetos import ErroValidacao
from ..textos import rotulos_locutor
from .app import com_projeto, json_ok


def _f(nome, padrao=""):
    return (request.form.get(nome) or padrao).strip()


def _i(nome, padrao=None):
    v = request.values.get(nome)
    try:
        return int(v) if v not in (None, "") else padrao
    except ValueError:
        return padrao


def _j():
    return request.get_json(silent=True) or {}


def segmentos(texto: str, intervalos: list[tuple[int, int, str]], rotulos: bool = True) -> list[dict]:
    """Divide o texto em segmentos não sobrepostos; cada um sabe quais intervalos o cobrem."""
    pontos = {0, len(texto)}
    for a, b, _ in intervalos:
        pontos.add(max(0, min(a, len(texto))))
        pontos.add(max(0, min(b, len(texto))))
    rot = rotulos_locutor(texto) if rotulos else []
    linhas_rotulo = []
    for a, b, _ in rot:
        ini_linha = texto.rfind("\n", 0, a) + 1
        fim_linha = texto.find("\n", b)
        fim_linha = len(texto) if fim_linha < 0 else fim_linha
        linhas_rotulo.append((ini_linha, fim_linha))
        pontos.update((ini_linha, fim_linha))
    pts = sorted(pontos)
    out = []
    for a, b in zip(pts, pts[1:]):
        if a == b:
            continue
        ids = [k for x, y, k in intervalos if x <= a and b <= y]
        out.append({"i": a, "f": b, "t": texto[a:b], "ids": ids,
                    "rotulo": any(x <= a and b <= y for x, y in linhas_rotulo)})
    return out


def registrar(app):  # noqa: C901 — registro central das rotas
    # ================================================================== início e projetos
    @app.route("/")
    def inicio():
        return render_template("inicio.html", projetos=projetos.listar(), backups=backup.listar()[:8],
                               pasta_dados=str(config.pasta_dados()))

    @app.route("/projetos/novo", methods=["GET", "POST"])
    def projeto_novo():
        listas = projetos.listas_predefinidas()
        if request.method == "POST":
            if _f("senha") != _f("senha2"):
                flash("As senhas do cofre não conferem.", "erro")
                return render_template("projeto_novo.html", listas=listas, dados=request.form)
            termos = []
            for nome in request.form.getlist("listas"):
                termos += listas.get(nome, [])
            termos += [t for t in _f("termos_extra").splitlines() if t.strip()]
            try:
                uid, codigo = projetos.criar(request.form, request.form.get("senha", ""), termos_protegidos=termos)
            except (ErroValidacao, CofreErro) as exc:
                flash(str(exc), "erro")
                return render_template("projeto_novo.html", listas=listas, dados=request.form)
            session["codigo_recuperacao"] = {uid: codigo}
            return redirect(url_for("recuperacao", uid=uid))
        return render_template("projeto_novo.html", listas=listas, dados={})

    @app.route("/projetos/demo", methods=["POST"])
    def projeto_demo():
        uid, codigo = demo.criar_projeto_demo()
        session["codigo_recuperacao"] = {uid: codigo}
        flash(f"Projeto de demonstração criado com dados fictícios. Senha do cofre: {demo.SENHA_DEMO}", "sucesso")
        return redirect(url_for("recuperacao", uid=uid))

    @app.route("/p/<uid>/recuperacao")
    @com_projeto
    def recuperacao():
        codigo = (session.get("codigo_recuperacao") or {}).get(g.p.uid)
        return render_template("recuperacao.html", codigo=codigo)

    @app.route("/p/<uid>/recuperacao/confirmar", methods=["POST"])
    @com_projeto
    def recuperacao_confirmar():
        if request.form.get("confirmo") != "1":
            flash("Marque a confirmação de que você guardou o código.", "erro")
            return redirect(url_for("recuperacao", uid=g.p.uid))
        cofre_mod.confirmar_recuperacao(g.p.con_cofre)
        session.pop("codigo_recuperacao", None)
        flash("Código de recuperação confirmado.", "sucesso")
        return redirect(url_for("painel_projeto", uid=g.p.uid))

    @app.route("/restaurar", methods=["POST"])
    def restaurar_backup_upload():
        arq = request.files.get("arquivo")
        if not arq or not arq.filename:
            flash("Escolha um arquivo .atenabak.", "erro")
            return redirect(url_for("inicio"))
        destino = config.pasta_backups() / "importados"
        destino.mkdir(exist_ok=True)
        caminho = destino / Path(arq.filename).name
        arq.save(caminho)
        try:
            uid = backup.restaurar(caminho, _f("modo", "copia"), _f("justificativa"))
        except ErroValidacao as exc:
            flash(str(exc), "erro")
            return redirect(url_for("inicio"))
        flash("Backup restaurado. Desbloqueie o cofre com a senha que valia quando o backup foi feito.", "sucesso")
        return redirect(url_for("painel_projeto", uid=uid))

    @app.route("/encerrar", methods=["POST"])
    def encerrar():
        cofres.bloquear()
        if not app.config.get("TESTING"):
            threading.Timer(0.8, lambda: os._exit(0)).start()
        return render_template("erro.html", titulo="ATENA encerrado",
                               mensagem="O programa foi encerrado com segurança. Você já pode fechar esta aba.")

    @app.route("/abrir-pasta", methods=["POST"])
    def abrir_pasta():
        alvo = {"dados": config.pasta_dados(), "exportacoes": config.pasta_exportacoes(),
                "backups": config.pasta_backups(), "logs": config.pasta_logs()}.get(_f("qual"))
        if alvo and sys.platform == "win32":
            os.startfile(str(alvo))  # noqa: S606 — abre o Explorer na pasta do próprio ATENA
        elif alvo:
            flash(f"Pasta: {alvo}", "info")
        return redirect(request.referrer or url_for("inicio"))

    # ================================================================== configurações gerais
    @app.route("/configuracoes")
    def configuracoes():
        chave, origem = segredos.obter_chave()
        return render_template("configuracoes.html", c=config.carregar(), tem_chave=bool(chave),
                               chave_mascara=segredos.mascarar(chave), origem_chave=origem,
                               mecanismo=segredos.DESCRICAO_MECANISMO.get(origem or segredos.mecanismo()),
                               pasta_dados=str(config.pasta_dados()), backups=backup.listar()[:20])

    @app.route("/configuracoes/salvar", methods=["POST"])
    def configuracoes_salvar():
        grupo = _f("grupo")
        alt = {}
        if grupo == "ia":
            alt = {"ia_habilitada": request.form.get("ia_habilitada") == "1", "ia_modelo": _f("ia_modelo") or "gpt-6-luna",
                   "ia_url_base": _f("ia_url_base") or "https://api.openai.com/v1"}
            for k in ("ia_preco_entrada", "ia_preco_saida", "ia_limite_mensal_usd", "ia_temperatura"):
                try:
                    alt[k] = float(_f(k).replace(",", "."))
                except ValueError:
                    pass
            for k in ("ia_max_caracteres_envio", "ia_tempo_limite_s"):
                try:
                    alt[k] = int(_f(k))
                except ValueError:
                    pass
        elif grupo == "backup":
            alt = {"backup_automatico": request.form.get("backup_automatico") == "1", "backup_pasta": _f("backup_pasta")}
            for k in ("backup_intervalo_horas", "backup_reter_ultimos", "cofre_bloqueio_minutos"):
                try:
                    alt[k] = max(0, float(_f(k)) if k == "backup_intervalo_horas" else int(_f(k)))
                except ValueError:
                    pass
        config.salvar(alt)
        flash("Configurações salvas.", "sucesso")
        return redirect(url_for("configuracoes") + f"#{grupo}")

    @app.route("/configuracoes/chave", methods=["POST"])
    def configuracoes_chave():
        if _f("acao") == "remover":
            segredos.remover_chave()
            flash("Chave removida deste computador.", "sucesso")
        else:
            chave = _f("chave")
            if not chave.startswith("sk-"):
                flash("A chave da OpenAI normalmente começa com “sk-”. Confira se copiou a chave inteira.", "alerta")
            if chave:
                m = segredos.salvar_chave(chave)
                flash("Chave guardada. " + segredos.DESCRICAO_MECANISMO[m], "sucesso")
        return redirect(url_for("configuracoes") + "#ia")

    @app.route("/api/configuracoes/testar-ia", methods=["POST"])
    def configuracoes_testar():
        return jsonify(ia.testar_conexao())

    @app.route("/configuracoes/backup-completo", methods=["POST"])
    def backup_completo():
        arq = backup.backup_completo()
        flash(f"Backup completo gerado: {arq.name}", "sucesso")
        return send_file(arq, as_attachment=True, download_name=arq.name)

    @app.route("/configuracoes/pacote", methods=["POST"])
    def abrir_pacote():
        arq = request.files.get("arquivo")
        if not arq:
            flash("Escolha o arquivo .atenaseg.", "erro")
            return redirect(url_for("configuracoes"))
        dados = exportacao.abrir_pacote_protegido(arq.read(), request.form.get("senha", ""))
        return render_template("pacote.html", dados=dados)

    @app.route("/backups/baixar")
    def backup_baixar():
        caminho = Path(request.args.get("arquivo", "")).resolve()
        if config.pasta_backups().resolve() not in caminho.parents or not caminho.is_file():
            abort(404)
        return send_file(caminho, as_attachment=True, download_name=caminho.name)

    @app.route("/backups/restaurar", methods=["POST"])
    def backup_restaurar_local():
        caminho = Path(_f("arquivo")).resolve()
        if config.pasta_backups().resolve() not in caminho.parents or not caminho.is_file():
            abort(404)
        modo = _f("modo", "copia")
        if modo == "substituir" and _f("confirmacao") != "SUBSTITUIR":
            flash("Para substituir o projeto atual, digite SUBSTITUIR na confirmação.", "erro")
            return redirect(request.referrer or url_for("inicio"))
        if g.get("p"):
            g.p.fechar()
        uid = backup.restaurar(caminho, modo, _f("justificativa"))
        flash("Backup restaurado com sucesso. A restauração foi registrada no histórico.", "sucesso")
        return redirect(url_for("painel_projeto", uid=uid))

    # ================================================================== painel do projeto
    @app.route("/p/<uid>/")
    @com_projeto
    def painel_projeto():
        return render_template("painel.html", d=painel.painel(g.p))

    @app.route("/p/<uid>/projeto", methods=["GET", "POST"])
    @com_projeto
    def projeto_editar():
        if request.method == "POST":
            projetos.atualizar(g.p, request.form, _f("justificativa"))
            flash("Projeto atualizado.", "sucesso")
            return redirect(url_for("projeto_editar", uid=g.p.uid))
        return render_template("projeto.html", protegidos=projetos.listar_termos(g.p, "protegido"),
                               excecoes=projetos.listar_termos(g.p, "excecao"), listas=projetos.listas_predefinidas(),
                               campos=json.loads(g.p.dados()["campos_participante"] or "[]"))

    @app.route("/p/<uid>/projeto/situacao", methods=["POST"])
    @com_projeto
    def projeto_situacao():
        projetos.alterar_situacao(g.p, _f("situacao"), _f("justificativa"))
        flash("Situação do projeto alterada.", "sucesso")
        return redirect(url_for("projeto_editar", uid=g.p.uid))

    @app.route("/p/<uid>/projeto/termos", methods=["POST"])
    @com_projeto
    def projeto_termos():
        termos = [t for t in _f("termos").splitlines() if t.strip()]
        lista = _f("lista")
        if lista:
            termos += projetos.listas_predefinidas().get(lista, [])
        n = projetos.adicionar_termos(g.p, _f("tipo"), termos, _f("observacao"))
        flash(f"{n} termo(s) adicionado(s).", "sucesso")
        return redirect(url_for("projeto_editar", uid=g.p.uid) + "#privacidade")

    @app.route("/p/<uid>/projeto/termos/<int:tid>/remover", methods=["POST"])
    @com_projeto
    def projeto_termo_remover(tid):
        projetos.remover_termo(g.p, tid, _f("justificativa"))
        flash("Termo desativado (fica registrado no histórico).", "sucesso")
        return redirect(url_for("projeto_editar", uid=g.p.uid) + "#privacidade")

    @app.route("/api/p/<uid>/termos", methods=["POST"])
    @com_projeto
    def api_termo():
        d = _j()
        projetos.adicionar_termos(g.p, d.get("tipo", "excecao"), [d.get("termo", "")], d.get("observacao", ""))
        return json_ok()

    @app.route("/p/<uid>/projeto/campos", methods=["POST"])
    @com_projeto
    def projeto_campos():
        projetos.definir_campos_participante(g.p, _f("campos").splitlines())
        flash("Campos de participante atualizados.", "sucesso")
        return redirect(url_for("projeto_editar", uid=g.p.uid) + "#campos")

    # ================================================================== cofre
    @app.route("/p/<uid>/cofre", methods=["GET", "POST"])
    @com_projeto
    def cofre():
        proximo = request.values.get("proximo") or ""
        if request.method == "POST":
            try:
                dek = cofre_mod.abrir(g.p.con_cofre, request.form.get("senha", ""))
            except SenhaIncorreta:
                flash("Senha incorreta.", "erro")
                return redirect(url_for("cofre", uid=g.p.uid, proximo=proximo))
            cofres.guardar(g.p.uid, dek)
            flash("Cofre desbloqueado.", "sucesso")
            if proximo.startswith("/") and not proximo.startswith("//"):
                return redirect(proximo)
            return redirect(url_for("painel_projeto", uid=g.p.uid))
        acessos = linhas(g.p.con_cofre.execute("SELECT * FROM acessos ORDER BY id DESC LIMIT 30"))
        n_ident = g.p.con_cofre.execute("SELECT COUNT(*) FROM identidades WHERE ativo = 1").fetchone()[0]
        return render_template("cofre.html", proximo=proximo, acessos=acessos, n_ident=n_ident,
                               recuperacao_ok=cofre_mod.recuperacao_confirmada(g.p.con_cofre),
                               categorias=protegido.CATEGORIAS, minutos=config.obter("cofre_bloqueio_minutos"))

    @app.route("/p/<uid>/cofre/bloquear", methods=["POST"])
    @com_projeto
    def cofre_bloquear():
        cofres.bloquear(g.p.uid)
        flash("Cofre bloqueado.", "sucesso")
        return redirect(url_for("painel_projeto", uid=g.p.uid))

    @app.route("/p/<uid>/cofre/senha", methods=["POST"])
    @com_projeto
    def cofre_senha():
        if _f("nova") != _f("nova2"):
            flash("As novas senhas não conferem.", "erro")
        else:
            try:
                cofre_mod.trocar_senha(g.p.con_cofre, request.form.get("atual", ""), request.form.get("nova", ""))
                flash("Senha do cofre alterada.", "sucesso")
            except SenhaIncorreta:
                flash("Senha atual incorreta.", "erro")
        return redirect(url_for("cofre", uid=g.p.uid))

    @app.route("/p/<uid>/cofre/recuperar", methods=["POST"])
    @com_projeto
    def cofre_recuperar():
        if _f("nova") != _f("nova2"):
            flash("As novas senhas não conferem.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        try:
            dek = cofre_mod.abrir_com_recuperacao(g.p.con_cofre, _f("codigo"), request.form.get("nova", ""))
        except SenhaIncorreta:
            flash("Código de recuperação incorreto.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        cofres.guardar(g.p.uid, dek)
        flash("Senha redefinida com o código de recuperação. Gere um novo código de recuperação.", "sucesso")
        return redirect(url_for("cofre", uid=g.p.uid))

    @app.route("/p/<uid>/cofre/novo-codigo", methods=["POST"])
    @com_projeto
    def cofre_novo_codigo():
        cofre_mod.abrir(g.p.con_cofre, request.form.get("senha", ""))
        codigo = cofre_mod.novo_codigo_recuperacao(g.p.con_cofre, g.p.dek())
        session["codigo_recuperacao"] = {g.p.uid: codigo}
        return redirect(url_for("recuperacao", uid=g.p.uid))

    @app.route("/p/<uid>/cofre/mapeamento", methods=["POST"])
    @com_projeto
    def cofre_mapeamento():
        motivo = _f("motivo")
        if not motivo:
            flash("Informe o motivo da consulta ao mapeamento.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        try:
            cofre_mod.abrir(g.p.con_cofre, request.form.get("senha", ""))
        except SenhaIncorreta:
            flash("Senha incorreta.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        ids = protegido.identidades(g.p, registrar_consulta=True, motivo=motivo)
        return render_template("mapeamento.html", identidades=ids, categorias=protegido.CATEGORIAS)

    @app.route("/p/<uid>/cofre/identidade", methods=["POST"])
    @com_projeto
    def cofre_identidade():
        variantes = [v for v in _f("variantes").split(";") if v.strip()]
        if _f("codigo_existente"):
            protegido.atualizar_identidade(g.p, _f("codigo_existente"), valor=_f("valor") or None, variantes=variantes)
            flash("Identidade atualizada.", "sucesso")
        else:
            i = protegido.criar_identidade(g.p, _f("categoria"), _f("valor"), variantes, codigo=_f("codigo") or None)
            flash(f"Valor registrado no cofre com o código {i['codigo']}.", "sucesso")
        return redirect(request.referrer or url_for("cofre", uid=g.p.uid))

    # ================================================================== participantes
    def _metadados_form():
        campos = request.form.getlist("meta_chave")
        valores = request.form.getlist("meta_valor")
        return {k: v for k, v in zip(campos, valores) if k.strip()}

    @app.route("/p/<uid>/participantes", methods=["GET", "POST"])
    @com_projeto
    def participantes():
        if request.method == "POST":
            nome = _f("nome_real")
            variantes = [v for v in _f("variantes").split(";") if v.strip()]
            pa = parts.criar(g.p, _metadados_form(), _f("situacao", "prevista"), _f("observacoes"), nome, variantes)
            flash(f"Participante {pa['codigo']} criado.", "sucesso")
            return redirect(url_for("participantes", uid=g.p.uid))
        return render_template("participantes.html", lista=parts.listar(g.p, incluir_inativos=True),
                               situacoes=parts.SITUACOES, campos=json.loads(g.p.dados()["campos_participante"] or "[]"))

    @app.route("/p/<uid>/participantes/<int:pid>", methods=["GET", "POST"])
    @com_projeto
    def participante(pid):
        if request.method == "POST":
            parts.editar(g.p, pid, metadados=_metadados_form(), situacao=_f("situacao"), observacoes=_f("observacoes"),
                         justificativa=_f("justificativa"))
            flash("Participante atualizado.", "sucesso")
            return redirect(url_for("participante", uid=g.p.uid, pid=pid))
        pa = parts.obter(g.p, pid)
        campos = json.loads(g.p.dados()["campos_participante"] or "[]")
        hist, _ = consultar(g.p.con, entidade="participante", entidade_id=str(pid), limite=50)
        documentos_ = [d for d in docs.listar(g.p) if d["participante_id"] == pid]
        return render_template("participante.html", pa=pa, campos=campos, situacoes=parts.SITUACOES, hist=hist,
                               documentos=documentos_)

    @app.route("/p/<uid>/participantes/<int:pid>/ativo", methods=["POST"])
    @com_projeto
    def participante_ativo(pid):
        if _f("acao") == "desativar":
            parts.desativar(g.p, pid, _f("justificativa"))
        else:
            parts.reativar(g.p, pid)
        return redirect(url_for("participante", uid=g.p.uid, pid=pid))

    @app.route("/p/<uid>/participantes/<int:pid>/identidade", methods=["POST"])
    @com_projeto
    def participante_identidade(pid):
        parts.definir_identidade(g.p, pid, _f("nome_real"), [v for v in _f("variantes").split(";") if v.strip()])
        flash("Identidade guardada no cofre (cifrada).", "sucesso")
        return redirect(url_for("participante", uid=g.p.uid, pid=pid))

    # ================================================================== documentos
    @app.route("/p/<uid>/documentos")
    @com_projeto
    def documentos():
        return render_template("documentos.html", lista=docs.listar(g.p, incluir_inativos=True),
                               participantes=parts.listar(g.p), tipos=docs.TIPOS,
                               formatos=", ".join(sorted(__import__("atena.servicos.importacao", fromlist=["x"]).FORMATOS)))

    @app.route("/p/<uid>/documentos/importar", methods=["POST"])
    @com_projeto
    def documentos_importar():
        g.p.dek()
        arquivos = [a for a in request.files.getlist("arquivos") if a and a.filename]
        if not arquivos:
            flash("Escolha ao menos um arquivo.", "erro")
            return redirect(url_for("documentos", uid=g.p.uid))
        ok = []
        for a in arquivos:
            try:
                d = docs.importar(g.p, a.filename, a.read(), titulo=_f("titulo") if len(arquivos) == 1 else "",
                                  tipo=_f("tipo", "entrevista"), participante_id=_i("participante_id"),
                                  ja_anonimizado=request.form.get("ja_anonimizado") == "1", data_coleta=_f("data_coleta"))
                ok.append(d["codigo"])
                for av in d.get("avisos", []):
                    flash(f"{d['codigo']}: {av}", "info")
            except ErroValidacao as exc:
                flash(f"{a.filename}: {exc}", "erro")
        if ok:
            flash(f"Importado(s): {', '.join(ok)}. Os originais foram preservados e cifrados.", "sucesso")
        return redirect(url_for("documentos", uid=g.p.uid))

    @app.route("/p/<uid>/documentos/colar", methods=["POST"])
    @com_projeto
    def documentos_colar():
        d = docs.criar_manual(g.p, request.form.get("texto", ""), titulo=_f("titulo"), tipo=_f("tipo", "entrevista"),
                              participante_id=_i("participante_id"), ja_anonimizado=request.form.get("ja_anonimizado") == "1",
                              data_coleta=_f("data_coleta"))
        flash(f"Documento {d['codigo']} criado.", "sucesso")
        return redirect(url_for("documento", uid=g.p.uid, did=d["id"]))

    @app.route("/p/<uid>/documentos/legado", methods=["POST"])
    @com_projeto
    def documentos_legado():
        z = request.files.get("zip")
        k = request.files.get("chave")
        if not z or not z.filename:
            flash("Envie o ZIP da pasta do sistema anterior.", "erro")
            return redirect(url_for("documentos", uid=g.p.uid))
        r = legado.importar(g.p, zip_bytes=z.read(), chave_fernet=k.read() if k and k.filename else None,
                            importar_excecoes=request.form.get("excecoes") == "1")
        flash(f"Importação concluída: {r['documentos']} transcrições, {r['participantes']} participantes, {r['termos']} termos "
              f"protegidos, {r['identidades']} identidades no cofre.", "sucesso")
        for a in r["avisos"]:
            flash(a, "alerta")
        return redirect(url_for("documentos", uid=g.p.uid))

    @app.route("/p/<uid>/documentos/<int:did>", methods=["GET", "POST"])
    @com_projeto
    def documento(did):
        if request.method == "POST":
            docs.editar_metadados(g.p, did, request.form, _f("justificativa"))
            flash("Documento atualizado.", "sucesso")
            return redirect(url_for("documento", uid=g.p.uid, did=did))
        d = docs.obter(g.p, did)
        protegidas = protegido.versoes_texto(g.p, did)
        hist, _ = consultar(g.p.con, entidade="documento", entidade_id=str(did), limite=30)
        return render_template("documento.html", d=d, versoes=tr.versoes(g.p, did), protegidas=protegidas,
                               original=protegido.info_original(g.p, did), participantes=parts.listar(g.p), tipos=docs.TIPOS,
                               sessoes=anon.sessoes(g.p, did), hist=hist,
                               nome_original=protegido.nome_original(g.p, did) if g.p.cofre_aberto() else None)

    @app.route("/p/<uid>/documentos/<int:did>/ativo", methods=["POST"])
    @com_projeto
    def documento_ativo(did):
        if _f("acao") == "arquivar":
            docs.desativar(g.p, did, _f("justificativa"))
            flash("Documento arquivado (nada foi apagado).", "sucesso")
        else:
            docs.reativar(g.p, did)
        return redirect(url_for("documento", uid=g.p.uid, did=did))

    @app.route("/p/<uid>/documentos/<int:did>/original")
    @com_projeto
    def documento_original(did):
        nome, dados = protegido.ler_original(g.p, did)
        return send_file(io.BytesIO(dados), as_attachment=True, download_name=nome)

    @app.route("/p/<uid>/documentos/<int:did>/protegido", methods=["GET", "POST"])
    @com_projeto
    def documento_protegido(did):
        if request.method == "POST":
            docs.salvar_revisao_protegida(g.p, did, request.form.get("texto", ""), _f("observacao"))
            flash("Nova versão revisada salva no cofre. Faça (ou refaça) a anonimização para usá-la na análise.", "sucesso")
            return redirect(url_for("documento_protegido", uid=g.p.uid, did=did))
        tipo = request.args.get("tipo", "revisado")
        t = protegido.ler_texto(g.p, did, tipo, _i("n"))
        return render_template("documento_protegido.html", d=docs.obter(g.p, did), t=t, versoes=protegido.versoes_texto(g.p, did))

    # ================================================================== anonimização
    @app.route("/p/<uid>/anonimizacao")
    @com_projeto
    def anonimizacao_lista():
        return render_template("anonimizacao_lista.html", lista=docs.listar(g.p))

    @app.route("/p/<uid>/anonimizacao/<int:did>", methods=["GET", "POST"])
    @com_projeto
    def anonimizacao(did):
        g.p.dek()
        if request.method == "POST":  # iniciar nova sessão (também para reanonimizar)
            anon.iniciar_sessao(g.p, did)
            return redirect(url_for("anonimizacao", uid=g.p.uid, did=did))
        s = anon.sessao_aberta(g.p, did)
        d = docs.obter(g.p, did)
        if not s:
            return render_template("anonimizacao_inicio.html", d=d, sessoes=anon.sessoes(g.p, did))
        t = protegido.ler_texto_id(g.p, s["texto_id"])
        its = anon.itens(g.p, s["id"], t["texto"])
        segs = segmentos(t["texto"], [(i["inicio"], i["fim"], i["id"]) for i in its], rotulos=False)
        mapa_itens = {i["id"]: {"d": i["decisao"], "c": i["categoria"], "cod": i["codigo"]} for i in its}
        from ..servicos.pii import AVISO
        return render_template("anonimizacao.html", d=d, s=s, t=t, itens=its, grupos=anon.grupos(its), segs=segs,
                               resumo=anon.resumo(its), categorias=protegido.CATEGORIAS, aviso=AVISO,
                               identidades=protegido.identidades(g.p), mapa_itens=mapa_itens,
                               protegidos=projetos.listar_termos(g.p, "protegido"))

    @app.route("/api/p/<uid>/anon/decidir", methods=["POST"])
    @com_projeto
    def api_anon_decidir():
        d = _j()
        n = anon.decidir(g.p, [int(x) for x in d.get("itens", [])], d.get("decisao", ""), categoria=d.get("categoria"),
                         codigo=d.get("codigo") or None, novo_codigo=bool(d.get("novo_codigo")), observacao=d.get("observacao", ""))
        if d.get("excecao") and d.get("decisao") == "rejeitar" and d.get("termo"):
            projetos.adicionar_termos(g.p, "excecao", [d["termo"]], "marcado como não-dado-pessoal na revisão")
        return json_ok(n=n)

    @app.route("/api/p/<uid>/anon/<int:sid>/manual", methods=["POST"])
    @com_projeto
    def api_anon_manual(sid):
        d = _j()
        ids = anon.adicionar_manual(g.p, sid, d["inicio"], d["fim"], d.get("categoria", "PESSOA"),
                                    todas_ocorrencias=bool(d.get("todas", True)))
        return json_ok(itens=ids)

    @app.route("/api/p/<uid>/anon/item/<int:iid>/ajustar", methods=["POST"])
    @com_projeto
    def api_anon_ajustar(iid):
        d = _j()
        anon.ajustar_trecho(g.p, iid, d["inicio"], d["fim"])
        return json_ok()

    @app.route("/p/<uid>/anonimizacao/sessao/<int:sid>/<acao>", methods=["POST"])
    @com_projeto
    def anonimizacao_acao(sid, acao):
        s = anon.obter_sessao(g.p, sid)
        if acao == "lote":
            n = anon.aprovar_sugestoes_altas(g.p, sid)
            flash(f"{n} item(ns) de confiança alta aprovados para anonimização.", "sucesso")
        elif acao == "aplicar":
            if request.form.get("confirmo") != "1":
                flash("Confirme que revisou o documento inteiro.", "erro")
                return redirect(url_for("anonimizacao", uid=g.p.uid, did=s["documento_id"]))
            r = anon.aplicar(g.p, sid, _f("justificativa"))
            flash(f"Anonimização aplicada: versão anonimizada nº {r['versao']['numero']} criada. "
                  f"{r['resumo']['anonimizar']} substituições.", "sucesso")
            if r["versao"].get("inconsistente"):
                flash(f"{r['versao']['inconsistente']} fragmento(s) codificado(s) precisam de revisão.", "alerta")
            for x in r["residuos"]:
                flash(f"Atenção: “{x['trecho']}” (registrado como {x['codigo']}) ainda aparece no texto anonimizado.", "alerta")
            return redirect(url_for("documento", uid=g.p.uid, did=s["documento_id"]))
        elif acao == "cancelar":
            anon.cancelar(g.p, sid, _f("justificativa"))
            flash("Revisão cancelada. Nenhuma alteração foi aplicada.", "sucesso")
        return redirect(url_for("anonimizacao", uid=g.p.uid, did=s["documento_id"]))

    @app.route("/p/<uid>/anonimizacao/sessao/<int:sid>/previa")
    @com_projeto
    def anonimizacao_previa(sid):
        s = anon.obter_sessao(g.p, sid)
        return render_template("anonimizacao_previa.html", d=docs.obter(g.p, s["documento_id"]), s=s,
                               texto=anon.previa(g.p, sid))

    # ================================================================== entrevistas (fase 1) e editor
    @app.route("/p/<uid>/entrevistas")
    @com_projeto
    def entrevistas():
        return render_template("entrevistas.html", lista=docs.listar(g.p), notas=notas.listar(g.p, fase=1)[:30],
                               fase=fases.obter(g.p, 1), leituras=docs.leituras(g.p)[:30], tipos_nota=notas.TIPOS)

    @app.route("/p/<uid>/editor")
    @com_projeto
    def editor_escolha():
        return render_template("editor_escolha.html", lista=docs.listar(g.p))

    @app.route("/p/<uid>/editor/<int:did>")
    @com_projeto
    def editor(did):
        d = docs.obter(g.p, did)
        v = tr.versao_atual(g.p, did)
        if not v:
            flash("Este documento ainda não foi anonimizado. Conclua a anonimização para lê-lo e codificá-lo.", "alerta")
            return redirect(url_for("documento", uid=g.p.uid, did=did))
        modo = request.args.get("modo", "codificar")
        frags = cod.fragmentos_documento(g.p, did)
        segs = segmentos(v["texto"], [(f["inicio"], f["fim"], f["id"]) for f in frags])
        docs.registrar_leitura(g.p, did, "abertura")
        lista = docs.listar(g.p)
        idx = [x["id"] for x in lista if x["versao_numero"]]
        pos = idx.index(did) if did in idx else -1
        return render_template("editor.html", d=d, v=v, modo=modo, segs=segs, frags=frags,
                               frags_json={f["id"]: {"c": [c["id"] for c in f["codigos"]], "cor": (f["codigos"][0]["cor"] if f["codigos"] else ""),
                                                     "s": f["situacao"], "r": f["relevante"]} for f in frags},
                               codigos=cod.listar_codigos(g.p), notas=notas.listar(g.p, documento_id=did),
                               anterior=idx[pos - 1] if pos > 0 else None, proximo=idx[pos + 1] if 0 <= pos < len(idx) - 1 else None,
                               tipos_nota=notas.TIPOS, n_leituras=len([l for l in docs.leituras(g.p, did) if l["tipo"] == "leitura_integral"]))

    @app.route("/p/<uid>/editor/<int:did>/salvar", methods=["POST"])
    @com_projeto
    def editor_salvar(did):
        r = tr.salvar_edicao(g.p, did, request.form.get("texto", ""), _f("observacao"), _i("versao_base"))
        flash(f"Versão nº {r['numero']} salva. Fragmentos: {r['ok']} intactos, {r['realocado']} realocados, "
              f"{r['inconsistente']} inconsistentes.", "sucesso")
        if r["realocado"] or r["inconsistente"]:
            flash("Alguns fragmentos codificados foram afetados pela edição. Revise-os em “Fragmentos a revisar”.", "alerta")
        for a in r["alertas_privacidade"]:
            flash(f"Possível dado pessoal introduzido na edição: “{a['trecho']}” ({a['categoria']}). Verifique.", "alerta")
        return redirect(url_for("editor", uid=g.p.uid, did=did, modo="codificar"))

    @app.route("/p/<uid>/editor/<int:did>/versoes")
    @com_projeto
    def editor_versoes(did):
        vs = tr.versoes(g.p, did)
        a, b = _i("a"), _i("b")
        comp = tr.comparar(g.p, a, b) if a and b else None
        return render_template("versoes.html", d=docs.obter(g.p, did), versoes=vs, comp=comp, a=a, b=b)

    @app.route("/p/<uid>/editor/<int:did>/restaurar", methods=["POST"])
    @com_projeto
    def editor_restaurar(did):
        r = tr.restaurar(g.p, did, _i("versao_id"), _f("justificativa"))
        flash(f"Versão restaurada como nova versão nº {r['numero']}.", "sucesso")
        return redirect(url_for("editor_versoes", uid=g.p.uid, did=did))

    @app.route("/p/<uid>/editor/<int:did>/leitura", methods=["POST"])
    @com_projeto
    def editor_leitura(did):
        docs.registrar_leitura(g.p, did, "leitura_integral", _f("observacao"))
        flash("Leitura integral registrada no diário de familiarização.", "sucesso")
        return redirect(request.referrer or url_for("editor", uid=g.p.uid, did=did, modo="leitura"))

    @app.route("/api/p/<uid>/editor/<int:did>/buscar")
    @com_projeto
    def api_buscar(did):
        return jsonify(tr.buscar(g.p, did, request.args.get("q", ""), request.args.get("exato") == "1"))

    # ------------------------------------------------------------------ fragmentos (API)
    @app.route("/api/p/<uid>/fragmentos", methods=["POST"])
    @com_projeto
    def api_fragmento_criar():
        d = _j()
        if d.get("so_relevante"):
            f = cod.criar_fragmento(g.p, int(d["documento_id"]), d["inicio"], d["fim"], relevante=True, nota=d.get("nota", ""),
                                    texto_esperado=d.get("texto"))
        else:
            f = cod.codificar_selecao(g.p, int(d["documento_id"]), d["inicio"], d["fim"], codigo_ids=d.get("codigos", []),
                                      novo_codigo=d.get("novo_codigo"), justificativa=d.get("justificativa", ""),
                                      texto_esperado=d.get("texto"))
        return json_ok(fragmento=f)

    @app.route("/api/p/<uid>/fragmentos/<int:fid>")
    @com_projeto
    def api_fragmento(fid):
        return jsonify(cod.obter_fragmento(g.p, fid))

    @app.route("/api/p/<uid>/fragmentos/<int:fid>/<acao>", methods=["POST"])
    @com_projeto
    def api_fragmento_acao(fid, acao):
        d = _j()
        if acao == "codigo":
            ids = [int(x) for x in d.get("codigos", [])]
            if (d.get("novo_codigo") or {}).get("nome"):
                ids.append(cod.criar_codigo(g.p, d["novo_codigo"]["nome"], d["novo_codigo"].get("descricao", ""),
                                            d["novo_codigo"].get("justificativa", ""))["id"])
            for cid in ids:
                cod.codificar(g.p, fid, cid, d.get("justificativa", ""))
        elif acao == "remover_codificacao":
            cod.remover_codificacao(g.p, int(d["codificacao_id"]), d.get("justificativa", ""))
        elif acao == "editar":
            cod.editar_fragmento(g.p, fid, nota=d.get("nota"), relevante=d.get("relevante"))
        elif acao == "desativar":
            cod.desativar_fragmento(g.p, fid, d.get("justificativa", ""))
        elif acao == "resolver":
            cod.resolver_situacao(g.p, fid, d.get("acao", ""), inicio=d.get("inicio"), fim=d.get("fim"),
                                  justificativa=d.get("justificativa", ""))
        elif acao == "nota":
            notas.criar(g.p, d.get("texto", ""), tipo=d.get("tipo", "memo"), documento_id=d.get("documento_id"), fragmento_id=fid)
        else:
            abort(404)
        return json_ok(fragmento=cod.obter_fragmento(g.p, fid))

    @app.route("/p/<uid>/fragmentos/revisar")
    @com_projeto
    def fragmentos_pendentes():
        lista = cod.fragmentos_pendentes(g.p)
        for f in lista:
            v = tr.versao_atual(g.p, f["documento_id"])
            f["atual"] = v["texto"][f["inicio"]:f["fim"]] if v else ""
            f["contexto_atual"] = v["texto"][max(0, f["inicio"] - 120):f["fim"] + 120] if v else ""
            f["codigos"] = cod.obter_fragmento(g.p, f["id"])["codigos"]
        return render_template("fragmentos_pendentes.html", lista=lista)

    @app.route("/p/<uid>/fragmentos")
    @com_projeto
    def consulta_fragmentos():
        """Consulta dos dados de origem de qualquer visualização (filtros combináveis)."""
        cond, params = ["f.ativo = 1"], []
        filtros = {k: request.args.get(k) for k in ("codigo", "tema", "documento", "participante", "q", "relevante")}
        if filtros["codigo"]:
            cond.append("EXISTS (SELECT 1 FROM codificacoes cf WHERE cf.fragmento_id = f.id AND cf.ativo = 1 AND cf.codigo_id = ?)")
            params.append(int(filtros["codigo"]))
        if filtros["tema"]:
            ids = tm.ids_codigos_tema(g.p, int(filtros["tema"])) or {-1}
            cond.append(f"EXISTS (SELECT 1 FROM codificacoes cf WHERE cf.fragmento_id = f.id AND cf.ativo = 1 AND cf.codigo_id IN "
                        f"({','.join('?' * len(ids))}))")
            params += list(ids)
        if filtros["documento"]:
            cond.append("f.documento_id = ?"); params.append(int(filtros["documento"]))
        if filtros["participante"]:
            if filtros["participante"] == "0":
                cond.append("d.participante_id IS NULL")
            else:
                cond.append("d.participante_id = ?"); params.append(int(filtros["participante"]))
        if filtros["q"]:
            cond.append("f.texto LIKE ?"); params.append(f"%{filtros['q']}%")
        if filtros["relevante"]:
            cond.append("f.relevante = 1")
        lista = linhas(g.p.con.execute(
            "SELECT f.*, d.codigo AS documento_codigo, pa.codigo AS participante_codigo FROM fragmentos f JOIN documentos d"
            f" ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE {' AND '.join(cond)}"
            " ORDER BY d.codigo, f.inicio LIMIT 1000", params))
        for f in lista:
            f["codigos"] = [{"id": r[0], "nome": r[1], "cor": r[2]} for r in g.p.con.execute(
                "SELECT c.id, c.nome, c.cor FROM codificacoes cf JOIN codigos c ON c.id = cf.codigo_id WHERE cf.fragmento_id = ?"
                " AND cf.ativo = 1 AND c.situacao = 'ativo'", (f["id"],))]
        return render_template("fragmentos.html", lista=lista, filtros=filtros, codigos=cod.listar_codigos(g.p),
                               temas=tm.listar(g.p), documentos=docs.listar(g.p), participantes=parts.listar(g.p))

    # ================================================================== notas e decisões
    @app.route("/p/<uid>/notas", methods=["GET", "POST"])
    @com_projeto
    def notas_view():
        if request.method == "POST":
            notas.criar(g.p, request.form.get("texto", ""), tipo=_f("tipo", "observacao"), titulo=_f("titulo"),
                        fase=_i("fase"), documento_id=_i("documento_id"), entidade=_f("entidade"), entidade_id=_i("entidade_id"))
            flash("Nota registrada.", "sucesso")
            return redirect(request.referrer or url_for("notas_view", uid=g.p.uid))
        return render_template("notas.html", lista=notas.listar(g.p, tipo=request.args.get("tipo", ""), fase=_i("fase"),
                                                                 texto=request.args.get("q", "")),
                               tipos=notas.TIPOS, documentos=docs.listar(g.p), filtro=request.args)

    @app.route("/p/<uid>/notas/<int:nid>", methods=["POST"])
    @com_projeto
    def nota_acao(nid):
        if _f("acao") == "arquivar":
            notas.arquivar(g.p, nid, _f("motivo"))
            flash("Nota arquivada.", "sucesso")
        else:
            notas.editar(g.p, nid, request.form.get("texto", ""), _f("titulo"), _f("motivo"))
            flash("Nota atualizada (versão anterior preservada no histórico).", "sucesso")
        return redirect(request.referrer or url_for("notas_view", uid=g.p.uid))

    @app.route("/p/<uid>/decisoes", methods=["GET", "POST"])
    @com_projeto
    def decisoes():
        if request.method == "POST":
            if _i("id"):
                notas.editar_decisao(g.p, _i("id"), _f("titulo"), _f("descricao"), _f("justificativa"), _f("motivo"))
            else:
                notas.registrar_decisao(g.p, _f("titulo"), _f("descricao"), _f("justificativa"), _i("fase"))
            flash("Decisão metodológica registrada.", "sucesso")
            return redirect(url_for("decisoes", uid=g.p.uid))
        return render_template("decisoes.html", lista=notas.decisoes(g.p), fases_def=fases.FASES)

    # ================================================================== análise temática (fases)
    @app.route("/p/<uid>/analise")
    @com_projeto
    def analise():
        return render_template("analise.html", fases_=fases.listar(g.p), decisoes=notas.decisoes(g.p)[:5])

    @app.route("/p/<uid>/analise/fase/<int:n>/<acao>", methods=["POST"])
    @com_projeto
    def fase_acao(n, acao):
        if acao == "concluir":
            fases.concluir(g.p, n, _f("observacao"))
            flash(f"Fase {n} registrada como concluída por você. Ela pode ser reaberta a qualquer momento.", "sucesso")
        elif acao == "reabrir":
            fases.reabrir(g.p, n, _f("observacao"))
            flash(f"Fase {n} reaberta.", "sucesso")
        elif acao == "iniciar":
            fases.iniciar(g.p, n)
        return redirect(request.referrer or url_for("analise", uid=g.p.uid))

    # ================================================================== códigos
    @app.route("/p/<uid>/codigos", methods=["GET", "POST"])
    @com_projeto
    def codigos():
        if request.method == "POST":
            c = cod.criar_codigo(g.p, _f("nome"), _f("descricao"), _f("justificativa"))
            flash(f"Código “{c['nome']}” criado.", "sucesso")
            return redirect(url_for("codigos", uid=g.p.uid))
        situacao = request.args.get("situacao", "ativo")
        return render_template("codigos.html", lista=cod.listar_codigos(g.p, situacao=situacao if situacao != "todos" else ""),
                               situacao=situacao, fase=fases.obter(g.p, 2),
                               pendentes_ia=ia.listar_sugestoes(g.p, situacao="pendente", tipo="codigo"))

    @app.route("/p/<uid>/codigos/<int:cid>", methods=["GET", "POST"])
    @com_projeto
    def codigo(cid):
        if request.method == "POST":
            acao = _f("acao")
            if acao == "editar":
                cod.editar_codigo(g.p, cid, nome=_f("nome"), descricao=_f("descricao"), justificativa=_f("justificativa"),
                                  cor=_f("cor") or None, motivo=_f("motivo"))
                flash("Código atualizado.", "sucesso")
            elif acao == "excluir":
                cod.excluir_codigo(g.p, cid, _f("motivo"))
                flash("Código excluído (pode ser restaurado; histórico preservado).", "sucesso")
            elif acao == "restaurar":
                cod.restaurar_codigo(g.p, cid, _f("motivo"))
                flash("Código restaurado.", "sucesso")
            elif acao == "mesclar":
                cod.mesclar_codigos(g.p, [cid], _i("destino"), _f("motivo"))
                flash("Código mesclado no destino.", "sucesso")
                return redirect(url_for("codigo", uid=g.p.uid, cid=_i("destino")))
            return redirect(url_for("codigo", uid=g.p.uid, cid=cid))
        c = cod.obter_codigo(g.p, cid)
        ev = cod.evidencias(g.p, cid)
        hist, _ = consultar(g.p.con, entidade="codigo", entidade_id=str(cid), limite=60)
        por_doc = {}
        for f in ev:
            por_doc.setdefault((f["documento_codigo"], f["participante_codigo"]), []).append(f)
        return render_template("codigo.html", c=c, ev=ev, por_doc=por_doc, hist=hist, todos=cod.listar_codigos(g.p),
                               temas=tm.listar(g.p))

    @app.route("/p/<uid>/codigos/matriz")
    @com_projeto
    def codigos_matriz():
        return render_template("codigos_matriz.html", m=cod.matriz_codigos_documentos(g.p))

    @app.route("/api/p/<uid>/codigos")
    @com_projeto
    def api_codigos():
        return jsonify([{"id": c["id"], "nome": c["nome"], "cor": c["cor"], "descricao": c["descricao"], "n": c["n_fragmentos"]}
                        for c in cod.listar_codigos(g.p)])

    # ================================================================== temas (fases 3–5)
    @app.route("/p/<uid>/temas", methods=["GET", "POST"])
    @com_projeto
    def mapa():
        if request.method == "POST":
            t = tm.criar(g.p, _f("nome"), _f("descricao"), pai_id=_i("pai_id"), justificativa=_f("justificativa"),
                         codigo_ids=[int(x) for x in request.form.getlist("codigos")])
            flash(f"{'Subtema' if t['pai_id'] else 'Tema candidato'} “{t['nome']}” criado.", "sucesso")
            return redirect(request.referrer or url_for("mapa", uid=g.p.uid))
        return render_template("temas_mapa.html", arvore=tm.arvore(g.p), sem_tema=tm.codigos_sem_tema(g.p),
                               codigos=cod.listar_codigos(g.p), fase=fases.obter(g.p, 3),
                               pendentes_ia=ia.listar_sugestoes(g.p, situacao="pendente", tipo="agrupamento"))

    @app.route("/api/p/<uid>/mapa")
    @com_projeto
    def api_mapa():
        return jsonify(vz.mapa_tematico(g.p, request.args.get("coocorrencia") != "0"))

    @app.route("/api/p/<uid>/mapa/mover", methods=["POST"])
    @com_projeto
    def api_mapa_mover():
        d = _j()
        tm.mover_codigo(g.p, int(d["codigo_id"]), d.get("de") and int(d["de"]), d.get("para") and int(d["para"]),
                        d.get("justificativa", ""))
        return json_ok()

    @app.route("/p/<uid>/temas/<int:tid>", methods=["GET", "POST"])
    @com_projeto
    def tema(tid):
        if request.method == "POST":
            acao = _f("acao")
            if acao == "editar":
                tm.editar(g.p, tid, {k: request.form.get(k) for k in tm.CAMPOS_DEFINICAO if k in request.form}, _f("motivo"))
                flash("Tema atualizado.", "sucesso")
            elif acao == "add_codigos":
                for c in request.form.getlist("codigos"):
                    tm.atribuir_codigo(g.p, tid, int(c), _f("justificativa"))
                flash("Código(s) agrupado(s) no tema.", "sucesso")
            elif acao == "remover_codigo":
                tm.remover_codigo(g.p, tid, _i("codigo_id"), _f("justificativa"))
                flash("Código retirado do tema.", "sucesso")
            elif acao == "situacao":
                tm.alterar_situacao(g.p, tid, _f("situacao"), _f("justificativa"))
                flash("Situação do tema alterada.", "sucesso")
            elif acao == "reativar":
                tm.reativar(g.p, tid, _f("justificativa"))
            elif acao == "pai":
                tm.definir_pai(g.p, tid, _i("pai_id"), _f("justificativa"))
                flash("Hierarquia atualizada.", "sucesso")
            elif acao == "fundir":
                tm.fundir(g.p, [tid], _i("destino"), _f("justificativa"))
                flash("Tema fundido no destino.", "sucesso")
                return redirect(url_for("tema", uid=g.p.uid, tid=_i("destino")))
            elif acao == "dividir":
                novo = tm.dividir(g.p, tid, _f("novo_nome"), [int(x) for x in request.form.getlist("codigos")], _f("justificativa"),
                                  como_subtema=request.form.get("como_subtema") == "1")
                flash(f"Tema dividido: “{novo['nome']}” criado.", "sucesso")
            elif acao == "revisar":
                tm.revisar(g.p, tid, _i("nivel", 1), coerencia=_f("coerencia"), distincao=_f("distincao"),
                           sobreposicao=_f("sobreposicao"), decisao=_f("decisao"), observacao=_f("observacao"))
                flash("Revisão registrada.", "sucesso")
            elif acao == "evidencia":
                if request.form.get("marcar") == "1":
                    tm.marcar_evidencia(g.p, tid, _i("fragmento_id"), _f("comentario"))
                else:
                    tm.desmarcar_evidencia(g.p, tid, _i("fragmento_id"))
            return redirect(url_for("tema", uid=g.p.uid, tid=tid) + ("#" + _f("ancora") if _f("ancora") else ""))
        t = tm.obter(g.p, tid)
        ev = tm.evidencias(g.p, tid)
        hist, _ = consultar(g.p.con, entidade="tema", entidade_id=str(tid), limite=60)
        return render_template("tema.html", t=t, ev=ev, hist=hist, todos=tm.listar(g.p), codigos=cod.listar_codigos(g.p),
                               confronto=tm.confronto_documentos(g.p, tid), situacoes=tm.SITUACOES,
                               notas_tema=notas.listar(g.p, entidade="tema", entidade_id=tid))

    @app.route("/p/<uid>/revisao")
    @com_projeto
    def revisao():
        return render_template("temas_revisao.html", temas=tm.listar(g.p), diag=tm.diagnostico(g.p), fase=fases.obter(g.p, 4),
                               inativos=[t for t in tm.listar(g.p, incluir_inativos=True) if t["situacao"] in ("descartado", "fundido")])

    @app.route("/p/<uid>/definicao")
    @com_projeto
    def definicao():
        return render_template("temas_definicao.html", temas=tm.listar(g.p), fase=fases.obter(g.p, 5))

    @app.route("/p/<uid>/temas/<int:tid>/definir", methods=["GET", "POST"])
    @com_projeto
    def tema_definir(tid):
        if request.method == "POST":
            r = tm.definir(g.p, tid, {k: request.form.get(k, "") for k in tm.CAMPOS_DEFINICAO},
                           confirmar=_f("confirmar") == "1")
            if _f("confirmar") == "1":
                flash("Definição confirmada.", "sucesso")
            else:
                flash("Rascunho da definição salvo." + (" Pendências: " + "; ".join(r["pendencias"]) if r["pendencias"] else ""), "info")
            return redirect(url_for("tema_definir", uid=g.p.uid, tid=tid))
        return render_template("tema_definir.html", t=tm.obter(g.p, tid), ev=tm.evidencias(g.p, tid), pergunta=g.p.dados()["pergunta"])

    # ================================================================== visualizações
    @app.route("/p/<uid>/visualizacoes")
    @com_projeto
    def visualizacoes():
        aba = request.args.get("aba", "mapa")
        filtros = {k: request.args.get(k) for k in ("documento", "participante", "codigo", "tema") if request.args.get(k)}
        dados = {}
        if aba == "frequencia":
            dados["freq"] = vz.frequencia_codigos(g.p, filtros)
        elif aba == "matriz":
            dados["m"] = vz.matriz_participantes(g.p, request.args.get("por", "codigo"), filtros)
        elif aba == "temas_entrevistas":
            dados["m"] = vz.matriz_temas_documentos(g.p)
        elif aba == "distribuicao":
            dados["dist"] = vz.distribuicao_temas(g.p)
        elif aba == "comparacao":
            a, b = _i("a"), _i("b")
            dados["comp"] = vz.comparar_documentos(g.p, a, b) if a and b else None
        elif aba == "linha_tempo":
            dados["eventos"] = vz.linha_do_tempo(g.p)
            dados["dias"] = vz.atividade_por_dia(g.p)
        elif aba == "lexical":
            dados["palavras"] = vz.frequencia_palavras(g.p, _i("documento"), request.args.get("todas") != "1",
                                                       ngrama=_i("n", 1) or 1)
            dados["kwic"] = vz.kwic(g.p, request.args.get("termo", "")) if request.args.get("termo") else []
        return render_template("visualizacoes.html", aba=aba, dados=dados, filtros=request.args,
                               documentos=docs.listar(g.p), participantes=parts.listar(g.p), codigos=cod.listar_codigos(g.p),
                               temas=tm.listar(g.p))

    # ================================================================== relatórios
    @app.route("/p/<uid>/relatorios", methods=["GET", "POST"])
    @com_projeto
    def relatorios():
        if request.method == "POST":
            rid = rel.gerar_rascunho(g.p, _f("tipo", "final"))
            flash("Rascunho gerado a partir dos dados da análise. Revise e complete o texto.", "sucesso")
            return redirect(url_for("relatorio", uid=g.p.uid, rid=rid))
        return render_template("relatorios.html", lista=rel.listar(g.p), tipos=rel.TIPOS, fase=fases.obter(g.p, 6),
                               exportacoes=exportacao.listar_exportacoes(g.p)[:15], conjuntos=exportacao.CONJUNTOS)

    @app.route("/p/<uid>/relatorios/<int:rid>", methods=["GET", "POST"])
    @com_projeto
    def relatorio(rid):
        if request.method == "POST":
            acao = _f("acao")
            if acao == "salvar":
                rel.salvar_versao(g.p, rid, request.form.get("conteudo", ""), _f("observacao"), versao_base=_i("versao_base"))
                flash("Nova versão do relatório salva.", "sucesso")
            elif acao == "aprovar":
                rel.aprovar(g.p, rid, _f("observacao"))
                flash("Versão aprovada pelo pesquisador.", "sucesso")
            elif acao == "restaurar":
                rel.restaurar_versao(g.p, rid, _i("numero"), _f("justificativa"))
                flash("Versão restaurada como nova versão.", "sucesso")
            elif acao == "arquivar":
                rel.arquivar(g.p, rid, _f("justificativa"))
                return redirect(url_for("relatorios", uid=g.p.uid))
            elif acao == "exportar":
                arq = exportacao.exportar_relatorio(g.p, rid, _f("formato", "docx"))
                return send_file(arq, as_attachment=True, download_name=arq.name)
            return redirect(url_for("relatorio", uid=g.p.uid, rid=rid))
        r = rel.obter(g.p, rid, _i("v"))
        frags = linhas(g.p.con.execute(
            "SELECT f.id, f.texto, d.codigo AS documento, pa.codigo AS participante FROM fragmentos f JOIN documentos d"
            " ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE f.ativo = 1"
            " AND EXISTS (SELECT 1 FROM codificacoes cf WHERE cf.fragmento_id = f.id AND cf.ativo = 1) ORDER BY d.codigo, f.inicio"))
        return render_template("relatorio.html", r=r, previa=rel.html_relatorio(g.p, r["versao"]["conteudo"]),
                               verificacao=rel.verificar(g.p, r["versao"]["conteudo"]), frags=frags, temas=tm.listar(g.p),
                               marca_ia=rel.MARCA_IA)

    @app.route("/api/p/<uid>/relatorios/previa", methods=["POST"])
    @com_projeto
    def api_relatorio_previa():
        c = _j().get("conteudo", "")
        return jsonify({"html": rel.html_relatorio(g.p, c), "verificacao": rel.verificar(g.p, c)})

    @app.route("/p/<uid>/exportar", methods=["POST"])
    @com_projeto
    def exportar():
        arq = exportacao.exportar_conjunto(g.p, _f("conjunto"), _f("formato"))
        return send_file(arq, as_attachment=True, download_name=arq.name)

    @app.route("/p/<uid>/exportar/protegido", methods=["POST"])
    @com_projeto
    def exportar_protegido():
        if request.form.get("ciente") != "1":
            flash("Confirme que está ciente de que o arquivo conterá dados identificáveis.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        try:
            arq = exportacao.exportar_mapeamento_protegido(g.p, request.form.get("senha_cofre", ""),
                                                           request.form.get("senha_exportacao", ""), _f("justificativa"))
        except SenhaIncorreta:
            flash("Senha do cofre incorreta.", "erro")
            return redirect(url_for("cofre", uid=g.p.uid))
        return send_file(arq, as_attachment=True, download_name=arq.name)

    @app.route("/p/<uid>/exportacoes/<path:arquivo>")
    @com_projeto
    def exportacao_baixar(arquivo):
        arq = exportacao.caminho_exportacao(g.p, arquivo)
        return send_file(arq, as_attachment=True, download_name=arq.name)

    # ================================================================== histórico
    @app.route("/p/<uid>/historico")
    @com_projeto
    def historico():
        pagina = max(1, _i("pagina", 1) or 1)
        regs, total = consultar(g.p.con, texto=request.args.get("q", ""), entidade=request.args.get("entidade", ""),
                                origem=request.args.get("origem", ""), operacao=request.args.get("operacao", ""),
                                entidade_id=request.args.get("entidade_id", ""), desde=request.args.get("desde", ""),
                                ate=request.args.get("ate", ""), limite=100, deslocamento=(pagina - 1) * 100)
        for h in regs:
            h["restauravel"] = restauracao.restauravel(h | {"antes": h["antes_obj"]})
        ops = [r[0] for r in g.p.con.execute("SELECT DISTINCT operacao FROM historico ORDER BY operacao")]
        return render_template("historico.html", regs=regs, total=total, pagina=pagina, filtros=request.args,
                               entidades=ROTULOS_ENTIDADE, operacoes=ops, cadeia=verificar_cadeia(g.p.con))

    @app.route("/p/<uid>/historico/<int:hid>/restaurar", methods=["POST"])
    @com_projeto
    def historico_restaurar(hid):
        flash(restauracao.restaurar(g.p, hid, _f("justificativa")), "sucesso")
        return redirect(url_for("historico", uid=g.p.uid))

    @app.route("/p/<uid>/integridade")
    @com_projeto
    def integridade():
        return render_template("integridade.html", r=painel.integridade(g.p))

    # ================================================================== backup do projeto
    @app.route("/p/<uid>/backup", methods=["GET", "POST"])
    @com_projeto
    def backup_projeto():
        if request.method == "POST":
            uid = g.p.uid
            g.p.fechar()
            g.pop("p")
            arq = backup.criar_backup(uid, "manual", _f("observacao"))
            flash(f"Backup criado: {arq.name}", "sucesso")
            return redirect(url_for("backup_projeto", uid=uid))
        return render_template("backup.html", lista=backup.listar(g.p.uid), c=config.carregar())

    # ================================================================== IA
    @app.route("/p/<uid>/ia")
    @com_projeto
    def ia_painel():
        return render_template("ia.html", interacoes=ia.listar_interacoes(g.p), sugestoes=ia.listar_sugestoes(g.p, situacao="pendente"),
                               decididas=ia.listar_sugestoes(g.p)[:60], gasto=ia.gasto_mes(g.p), c=config.carregar(),
                               temas=tm.listar(g.p), codigos=cod.listar_codigos(g.p), documentos=docs.listar(g.p),
                               relatorios=rel.listar(g.p), tem_chave=bool(segredos.obter_chave()[0]))

    @app.route("/p/<uid>/ia/preparar", methods=["POST"])
    @com_projeto
    def ia_preparar():
        op = _f("operacao")
        if request.is_json:
            params = _j().get("params", {})
            op = _j().get("operacao", op)
        else:
            params = {"tema_id": _i("tema_id"), "temas": request.form.getlist("temas"), "codigos": request.form.getlist("codigos"),
                      "documento_id": _i("documento_id"), "relatorio_id": _i("relatorio_id"),
                      "fragmentos": request.form.getlist("fragmentos")}
            if _i("documento_id") and op == "verificar_privacidade":
                params["inicio"], params["fim"] = _i("inicio", 0), _i("fim", 10 ** 9)
        i = ia.preparar(g.p, op, params)
        if request.is_json:
            return json_ok(url=url_for("ia_interacao", uid=g.p.uid, iid=i["id"]))
        return redirect(url_for("ia_interacao", uid=g.p.uid, iid=i["id"]))

    @app.route("/p/<uid>/ia/<int:iid>", methods=["GET", "POST"])
    @com_projeto
    def ia_interacao(iid):
        if request.method == "POST":
            if _f("acao") == "cancelar":
                ia.cancelar(g.p, iid)
                flash("Envio cancelado. Nada foi enviado.", "sucesso")
                return redirect(url_for("ia_painel", uid=g.p.uid))
            ia.enviar(g.p, iid, confirmo_anonimizado=request.form.get("confirmo_anonimizado") == "1",
                      confirmo_envio=request.form.get("confirmo_envio") == "1",
                      revisei_alertas=request.form.get("revisei_alertas") == "1")
            flash("Resposta recebida. As sugestões estão pendentes da sua decisão.", "sucesso")
            return redirect(url_for("ia_interacao", uid=g.p.uid, iid=iid))
        i = ia.obter_interacao(g.p, iid)
        return render_template("ia_interacao.html", i=i, c=config.carregar(), tem_chave=bool(segredos.obter_chave()[0]),
                               gasto=ia.gasto_mes(g.p), conteudo_usuario=i["corpo"]["messages"][1]["content"],
                               sistema=i["corpo"]["messages"][0]["content"], temas={t["id"]: t["nome"] for t in tm.listar(g.p)},
                               codigos={c["id"]: c["nome"] for c in cod.listar_codigos(g.p)})

    @app.route("/p/<uid>/ia/sugestoes/<int:sid>", methods=["POST"])
    @com_projeto
    def ia_decidir(sid):
        decisao = _f("decisao")
        editado = None
        if decisao == "editada":
            editado = {k: request.form.get(k) for k in ("nome", "descricao", "justificativa", "tema", "texto") if request.form.get(k) is not None}
        ia.decidir_sugestao(g.p, sid, decisao, editado=editado, observacao=_f("observacao"))
        flash({"aceita": "Sugestão aceita e aplicada.", "editada": "Sugestão aceita com suas edições.",
               "rejeitada": "Sugestão rejeitada (registrada no histórico)."}[decisao], "sucesso")
        return redirect(request.referrer or url_for("ia_painel", uid=g.p.uid))
