"""Integração opcional com a API da OpenAI — sempre com prévia e autorização explícita.

Garantias implementadas:

* O conteúdo enviado é montado **somente** a partir do banco de trabalho
  (texto anonimizado). O cofre (mapeamento, originais) nunca é lido para montar
  um envio; ele é usado apenas para *verificar* que nenhum valor identificável
  conhecido aparece no conteúdo.
* Cada envio passa por: ``preparar`` (prévia + verificação de privacidade) →
  confirmação explícita do pesquisador → ``enviar``. Tudo fica registrado em
  ``ia_interacoes`` (conteúdo, finalidade, modelo, resposta, custo) e no histórico.
* A resposta vira **sugestões pendentes**. Citações e identificadores devolvidos
  pela IA são conferidos contra o material enviado; o que não confere é descartado
  e sinalizado. Nada é aplicado sem decisão do pesquisador.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime

from .. import config, segredos
from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from . import pii
from .projetos import ErroValidacao, Projeto

OPERACOES = {
    "sugerir_codigos": "Sugerir códigos iniciais para trechos selecionados",
    "sugerir_agrupamentos": "Sugerir agrupamentos de códigos em possíveis temas e relações entre códigos",
    "sugerir_nomes_tema": "Sugerir nomes para um tema",
    "sintetizar_tema": "Redigir síntese preliminar de um tema a partir das suas evidências",
    "revisar_coerencia": "Apontar possíveis problemas de coerência interna de um tema",
    "comparar_temas": "Comparar descrições de temas (distinção e sobreposição)",
    "verificar_privacidade": "Procurar possíveis dados pessoais remanescentes em texto já anonimizado",
    "organizar_evidencias": "Sugerir organização das evidências de um tema",
    "revisar_texto": "Revisar coerência e clareza de um texto de relatório",
}

SISTEMA = (
    "Você é um assistente de pesquisa qualitativa que apoia, sem substituir, um pesquisador que conduz análise "
    "temática indutiva segundo Braun e Clarke (2006). Regras obrigatórias: (1) use somente o material fornecido; "
    "(2) nunca invente falas, citações, participantes, códigos ou resultados; (3) toda citação deve ser cópia exata "
    "de um trecho fornecido e indicar o identificador da passagem ou fragmento; (4) os participantes aparecem apenas "
    "por códigos anônimos (ex.: PARTICIPANTE_001) — não tente identificá-los; (5) suas respostas são sugestões "
    "provisórias que o pesquisador vai avaliar; (6) responda em português brasileiro e exclusivamente no formato "
    "JSON solicitado."
)


def _compactar(t: str, n: int) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


# ------------------------------------------------------------------ montagem do conteúdo
def _fragmentos(p, ids):
    out = []
    for fid in ids:
        r = p.con.execute("SELECT f.id, f.texto, d.codigo AS doc, pa.codigo AS part FROM fragmentos f"
                          " JOIN documentos d ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id"
                          " WHERE f.id = ? AND f.ativo = 1", (int(fid),)).fetchone()
        if r:
            out.append(dict(r))
    return out


def montar(p: Projeto, operacao: str, params: dict) -> tuple[str, dict, list]:
    """Retorna (instrução ao modelo, material, registros referenciados)."""
    from . import codificacao, temas
    limite = int(config.obter("ia_max_caracteres_envio") or 24000)
    projeto = p.dados()
    contexto = {"pergunta_de_pesquisa": projeto["pergunta"] or "(não informada)",
                "abordagem": "análise temática indutiva (Braun e Clarke, 2006)"}

    if operacao == "sugerir_codigos":
        passagens, registros = [], []
        for i, s in enumerate(params.get("passagens", []), 1):
            v = p.con.execute("SELECT t.texto, d.codigo, pa.codigo FROM transcricao_versoes t JOIN documentos d"
                              " ON d.id = t.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id"
                              " WHERE t.documento_id = ? ORDER BY t.numero DESC LIMIT 1", (int(s["documento_id"]),)).fetchone()
            if not v:
                raise ErroValidacao("Documento sem versão anonimizada.")
            ini, fim = int(s["inicio"]), int(s["fim"])
            texto = v[0][ini:fim]
            passagens.append({"id": f"P{i}", "documento": v[1], "participante": v[2] or "(sem participante)", "texto": texto})
            registros.append({"tipo": "passagem", "id": f"P{i}", "documento_id": int(s["documento_id"]), "inicio": ini, "fim": fim})
        for fid in params.get("fragmentos", []):
            for f in _fragmentos(p, [fid]):
                k = f"F{f['id']}"
                passagens.append({"id": k, "documento": f["doc"], "participante": f["part"] or "(sem participante)", "texto": f["texto"]})
                registros.append({"tipo": "fragmento", "id": k, "fragmento_id": f["id"]})
        if not passagens:
            raise ErroValidacao("Selecione ao menos um trecho ou fragmento.")
        existentes = [{"id": c["id"], "nome": c["nome"], "descricao": _compactar(c["descricao"], 160)}
                      for c in codificacao.listar_codigos(p)][:150]
        material = {"contexto": contexto, "codigos_existentes": existentes, "passagens": passagens}
        instrucao = ("Leia as passagens e sugira códigos iniciais (rótulos curtos que descrevem características "
                     "interessantes dos dados, próximos da linguagem dos participantes). Quando um código existente "
                     "servir, indique o seu id em 'codigo_existente_id'. Para cada sugestão, indique as evidências como "
                     "cópias EXATAS de trechos das passagens. Formato: {\"sugestoes\": [{\"nome\": str, \"descricao\": str, "
                     "\"justificativa\": str, \"codigo_existente_id\": int|null, \"evidencias\": [{\"passagem\": \"P1\", "
                     "\"citacao\": str}]}]}")
    elif operacao == "sugerir_agrupamentos":
        cods = codificacao.listar_codigos(p)
        ids = {int(x) for x in params.get("codigos", [])} or {c["id"] for c in cods}
        itens, registros = [], []
        for c in cods:
            if c["id"] not in ids:
                continue
            ev = codificacao.evidencias(p, c["id"])[:3]
            itens.append({"id": c["id"], "nome": c["nome"], "descricao": _compactar(c["descricao"], 200),
                          "exemplos": [{"fragmento": f"F{e['id']}", "participante": e["participante_codigo"],
                                        "texto": _compactar(e["texto"], 220)} for e in ev]})
            registros.append({"tipo": "codigo", "codigo_id": c["id"]})
        if len(itens) < 2:
            raise ErroValidacao("São necessários ao menos dois códigos.")
        temas_atuais = [{"id": t["id"], "nome": t["nome"]} for t in temas.listar(p)]
        material = {"contexto": contexto, "codigos": itens, "temas_existentes": temas_atuais}
        instrucao = ("Sugira possíveis agrupamentos de códigos em temas candidatos (padrões de significado "
                     "compartilhado, e não meramente tópicos ou frequências) e possíveis relações entre códigos. "
                     "Use apenas ids de códigos fornecidos. Formato: {\"agrupamentos\": [{\"tema\": str, \"descricao\": str, "
                     "\"codigos\": [int], \"justificativa\": str}], \"relacoes\": [{\"codigo_a\": int, \"codigo_b\": int, "
                     "\"relacao\": str, \"justificativa\": str}]}")
    elif operacao in ("sugerir_nomes_tema", "sintetizar_tema", "revisar_coerencia", "organizar_evidencias"):
        t = temas.obter(p, int(params["tema_id"]))
        ev = temas.evidencias(p, t["id"])
        total, frag = 0, []
        for e in ev:
            item = {"id": f"F{e['id']}", "participante": e["participante_codigo"] or "(sem participante)",
                    "documento": e["documento_codigo"], "codigos": [c["nome"] for c in e["codigos"] if c["no_tema"]],
                    "texto": e["texto"]}
            total += len(e["texto"])
            if total > limite:
                break
            frag.append(item)
        registros = [{"tipo": "tema", "tema_id": t["id"]}] + [{"tipo": "fragmento", "fragmento_id": int(f["id"][1:])} for f in frag]
        material = {"contexto": contexto,
                    "tema": {"id": t["id"], "nome": t["nome"], "descricao": t["descricao"], "conceito_central": t["conceito_central"],
                             "escopo": t["escopo"], "limites": t["limites"],
                             "codigos": [{"nome": c["nome"], "descricao": _compactar(c["descricao"], 200)} for c in t["codigos"]],
                             "subtemas": [s["nome"] for s in t["subtemas"]]},
                    "fragmentos": frag, "fragmentos_omitidos_por_limite": max(0, len(ev) - len(frag))}
        instrucao = {
            "sugerir_nomes_tema": "Sugira de 3 a 5 nomes concisos e analíticos para o tema, que capturem a sua essência. "
                                  "Formato: {\"nomes\": [{\"nome\": str, \"justificativa\": str}]}",
            "sintetizar_tema": "Redija uma síntese analítica preliminar do tema (2 a 4 parágrafos), apoiada apenas nos "
                               "fragmentos. Ao usar um fragmento, cite-o pelo identificador entre colchetes, ex.: [F12]. "
                               "Não reproduza falas que não estejam nos fragmentos. Formato: {\"sintese\": str, "
                               "\"fragmentos_citados\": [str]}",
            "revisar_coerencia": "Avalie a coerência interna do tema: indique fragmentos que parecem não se encaixar, "
                                 "possíveis subtemas e lacunas. Formato: {\"observacoes\": [{\"tipo\": "
                                 "\"coerencia\"|\"possivel_subtema\"|\"fragmento_destoante\"|\"lacuna\", \"texto\": str, "
                                 "\"fragmentos\": [str]}]}",
            "organizar_evidencias": "Sugira uma organização das evidências em grupos narrativos e indique as mais "
                                    "ilustrativas. Formato: {\"grupos\": [{\"titulo\": str, \"fragmentos\": [str], "
                                    "\"comentario\": str}], \"mais_ilustrativos\": [str]}",
        }[operacao]
    elif operacao == "comparar_temas":
        ts = [temas.obter(p, int(x)) for x in params.get("temas", [])]
        if len(ts) < 2:
            raise ErroValidacao("Selecione ao menos dois temas.")
        registros = [{"tipo": "tema", "tema_id": t["id"]} for t in ts]
        material = {"contexto": contexto, "temas": [{"id": t["id"], "nome": t["nome"], "descricao": t["descricao"],
                                                     "conceito_central": t["conceito_central"], "escopo": t["escopo"],
                                                     "limites": t["limites"], "codigos": [c["nome"] for c in t["codigos"]]}
                                                    for t in ts]}
        instrucao = ("Compare os temas quanto à distinção e à sobreposição. Formato: {\"observacoes\": [{\"temas\": "
                     "[int], \"tipo\": \"sobreposicao\"|\"distincao\"|\"sugestao\", \"texto\": str}]}")
    elif operacao == "verificar_privacidade":
        v = p.con.execute("SELECT t.texto, d.codigo FROM transcricao_versoes t JOIN documentos d ON d.id = t.documento_id"
                          " WHERE t.documento_id = ? ORDER BY t.numero DESC LIMIT 1", (int(params["documento_id"]),)).fetchone()
        if not v:
            raise ErroValidacao("Documento sem versão anonimizada.")
        ini, fim = int(params.get("inicio", 0)), int(params.get("fim", len(v[0])))
        texto = v[0][ini:fim][:limite]
        registros = [{"tipo": "passagem", "id": "P1", "documento_id": int(params["documento_id"]), "inicio": ini,
                      "fim": ini + len(texto)}]
        material = {"passagens": [{"id": "P1", "documento": v[1], "texto": texto}]}
        instrucao = ("O texto já passou por pseudonimização (códigos como PARTICIPANTE_001, PESSOA_002). Aponte trechos "
                     "que ainda possam identificar pessoas (nomes, apelidos, cargos únicos, locais muito específicos, "
                     "eventos singulares). Copie o trecho exatamente. Formato: {\"possiveis_identificadores\": "
                     "[{\"passagem\": \"P1\", \"trecho\": str, \"motivo\": str}]}")
    elif operacao == "revisar_texto":
        from . import relatorios
        rel = relatorios.obter(p, int(params["relatorio_id"]))
        texto = rel["versao"]["conteudo"][:limite]
        registros = [{"tipo": "relatorio", "relatorio_id": rel["id"], "versao": rel["versao"]["numero"]}]
        material = {"contexto": contexto, "texto": texto}
        instrucao = ("Revise a coerência, a clareza e a coesão do texto acadêmico, sem acrescentar resultados nem "
                     "citações. Preserve exatamente todas as marcações [[frag:N]]. Formato: {\"comentarios\": [str], "
                     "\"texto_revisado\": str}")
    else:
        raise ErroValidacao("Operação de IA desconhecida.")
    return instrucao, material, registros


def _mensagens(instrucao: str, material: dict) -> list[dict]:
    return [{"role": "system", "content": SISTEMA},
            {"role": "user", "content": instrucao + "\n\nMATERIAL (JSON):\n" + json.dumps(material, ensure_ascii=False, indent=1)}]


def estimar_custo(caracteres_entrada: int, tokens_saida: int = 1500) -> float:
    te = caracteres_entrada / 3.5
    return (te * float(config.obter("ia_preco_entrada")) + tokens_saida * float(config.obter("ia_preco_saida"))) / 1_000_000


def gasto_mes(p: Projeto) -> float:
    mes = datetime.now().strftime("%Y-%m")
    return p.con.execute("SELECT COALESCE(SUM(custo_estimado), 0) FROM ia_interacoes WHERE criado_em LIKE ?",
                         (f"{mes}%",)).fetchone()[0]


def preparar(p: Projeto, operacao: str, params: dict) -> dict:
    if operacao not in OPERACOES:
        raise ErroValidacao("Operação inválida.")
    instrucao, material, registros = montar(p, operacao, params)
    modelo = config.obter("ia_modelo")
    corpo = {"model": modelo, "temperature": float(config.obter("ia_temperatura")), "store": False,
             "response_format": {"type": "json_object"}, "messages": _mensagens(instrucao, material)}
    texto_usuario = corpo["messages"][1]["content"]
    verif = pii.verificar_envio(p, json.dumps(material, ensure_ascii=False))
    momento = agora()
    with transacao(p.con):
        cur = p.con.execute(
            "INSERT INTO ia_interacoes(operacao, finalidade, modelo, registros, conteudo_enviado, verificacao_privacidade,"
            " custo_estimado, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?)",
            (operacao, OPERACOES[operacao], modelo, json.dumps(registros), json.dumps(corpo, ensure_ascii=False),
             json.dumps(verif, ensure_ascii=False), estimar_custo(len(texto_usuario)), momento, momento))
        iid = cur.lastrowid
        registrar(p.con, "ia", iid, "preparar_envio", origem="pesquisador",
                  depois={"operacao": operacao, "modelo": modelo, "registros": registros, "caracteres": len(texto_usuario),
                          "possiveis_dados_pessoais": len(verif["itens"]), "bloqueantes": len(verif["bloqueantes"])})
    return obter_interacao(p, iid)


def obter_interacao(p: Projeto, iid: int) -> dict:
    r = linha(p.con.execute("SELECT * FROM ia_interacoes WHERE id = ?", (iid,)).fetchone())
    if not r:
        raise ErroValidacao("Interação não encontrada.")
    r["corpo"] = json.loads(r["conteudo_enviado"])
    r["verificacao"] = json.loads(r["verificacao_privacidade"] or "{}")
    r["registros_obj"] = json.loads(r["registros"] or "[]")
    r["operacao_rotulo"] = OPERACOES.get(r["operacao"], r["operacao"])
    r["sugestoes"] = listar_sugestoes(p, interacao_id=iid)
    return r


def listar_interacoes(p: Projeto, limite: int = 100) -> list[dict]:
    out = linhas(p.con.execute("SELECT id, operacao, finalidade, modelo, situacao, criado_em, confirmado_em, tokens_entrada,"
                               " tokens_saida, custo_estimado, erro FROM ia_interacoes ORDER BY id DESC LIMIT ?", (limite,)))
    for r in out:
        r["operacao_rotulo"] = OPERACOES.get(r["operacao"], r["operacao"])
        r["n_sugestoes"] = p.con.execute("SELECT COUNT(*) FROM sugestoes WHERE interacao_id = ?", (r["id"],)).fetchone()[0]
    return out


def cancelar(p: Projeto, iid: int) -> None:
    i = obter_interacao(p, iid)
    if i["situacao"] != "preparada":
        return
    with transacao(p.con):
        p.con.execute("UPDATE ia_interacoes SET situacao = 'cancelada', atualizado_em = ? WHERE id = ?", (agora(), iid))
        registrar(p.con, "ia", iid, "cancelar_envio", justificativa="Envio cancelado pelo pesquisador na prévia.")


# ------------------------------------------------------------------ chamada HTTP
class ErroIA(RuntimeError):
    pass


def _requisicao(metodo: str, caminho: str, corpo: dict | None, chave: str, tempo: float):
    url = config.obter("ia_url_base").rstrip("/") + caminho
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ErroIA("A URL da API precisa usar HTTPS.")
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Authorization": f"Bearer {chave}", "Content-Type": "application/json", "User-Agent": "ATENA/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=tempo, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        dica = {401: "Chave inválida ou revogada.", 403: "Sem permissão para este modelo/projeto.",
                404: "Modelo não encontrado — confira o nome do modelo nas configurações.",
                429: "Limite de uso ou de crédito atingido. Verifique o saldo em platform.openai.com/usage."}.get(exc.code, "")
        raise ErroIA(f"Erro {exc.code} da API. {dica} {msg}".strip()) from exc
    except urllib.error.URLError as exc:
        raise ErroIA(f"Não foi possível conectar à API (sem internet ou bloqueio de rede): {exc.reason}") from exc
    except TimeoutError as exc:
        raise ErroIA("A API demorou demais para responder.") from exc


def testar_conexao() -> dict:
    chave, origem = segredos.obter_chave()
    if not chave:
        return {"ok": False, "mensagem": "Nenhuma chave configurada."}
    try:
        r = _requisicao("GET", "/models", None, chave, 20)
    except ErroIA as exc:
        return {"ok": False, "mensagem": str(exc)}
    modelos = sorted(m.get("id", "") for m in r.get("data", []))
    alvo = config.obter("ia_modelo")
    return {"ok": True, "mensagem": "Conexão bem-sucedida." + ("" if alvo in modelos else
                                                                f" Atenção: o modelo “{alvo}” não aparece na lista da sua conta."),
            "modelo_disponivel": alvo in modelos, "modelos": [m for m in modelos if m.startswith(("gpt", "o"))][:60],
            "origem_chave": origem}


def enviar(p: Projeto, iid: int, *, confirmo_anonimizado: bool, confirmo_envio: bool,
           revisei_alertas: bool = False) -> dict:
    i = obter_interacao(p, iid)
    if i["situacao"] != "preparada":
        raise ErroValidacao("Esta interação já foi enviada ou cancelada.")
    if not (confirmo_anonimizado and confirmo_envio):
        raise ErroValidacao("É preciso confirmar que o conteúdo está anonimizado e autorizar o envio.")
    if not config.obter("ia_habilitada"):
        raise ErroValidacao("A integração com IA está desativada em Configurações.")
    verif = i["verificacao"]
    if any(b["fonte"] == "mapeamento" for b in verif.get("bloqueantes", [])):
        raise ErroValidacao("O conteúdo contém valores registrados no mapeamento de identidades. O envio foi bloqueado: "
                            "corrija a anonimização desses trechos antes de usar a IA.")
    if verif.get("bloqueantes") and not revisei_alertas:
        raise ErroValidacao("Há possíveis dados pessoais de alta confiança no conteúdo. Revise os alertas e confirme "
                            "que não são dados pessoais para prosseguir.")
    limite = float(config.obter("ia_limite_mensal_usd") or 0)
    if limite and gasto_mes(p) + i["custo_estimado"] > limite:
        raise ErroValidacao(f"O limite mensal de gastos configurado (US$ {limite:.2f}) seria ultrapassado.")
    chave, _ = segredos.obter_chave()
    if not chave:
        raise ErroValidacao("Nenhuma chave de API configurada (Configurações › Inteligência artificial).")
    momento = agora()
    with transacao(p.con):
        p.con.execute("UPDATE ia_interacoes SET situacao = 'enviada', confirmado_em = ?, atualizado_em = ? WHERE id = ?",
                      (momento, momento, iid))
        registrar(p.con, "ia", iid, "autorizar_envio", origem="pesquisador",
                  depois={"modelo": i["modelo"], "operacao": i["operacao"], "registros": i["registros_obj"],
                          "confirmou_anonimizacao": True, "revisou_alertas": revisei_alertas},
                  justificativa="Envio autorizado explicitamente na tela de prévia.")
    try:
        tempo = float(config.obter("ia_tempo_limite_s"))
        corpo = dict(i["corpo"])
        for _tentativa in range(4):
            try:
                resp = _requisicao("POST", "/chat/completions", corpo, chave, tempo)
                break
            except ErroIA as exc:
                # alguns modelos não aceitam certos parâmetros (modo JSON, temperatura, store):
                # repete o MESMO conteúdo autorizado, apenas sem o parâmetro recusado
                recusado = next((k for k in ("response_format", "temperature", "store") if k in str(exc) and k in corpo), None)
                if not recusado:
                    raise
                corpo.pop(recusado)
        else:
            raise ErroIA("A API recusou os parâmetros da requisição.")
        conteudo = resp["choices"][0]["message"]["content"]
        uso = resp.get("usage", {}) or {}
        te, ts = int(uso.get("prompt_tokens", 0)), int(uso.get("completion_tokens", 0))
        custo = (te * float(config.obter("ia_preco_entrada")) + ts * float(config.obter("ia_preco_saida"))) / 1_000_000
        dados = _json_da_resposta(conteudo)
    except (ErroIA, KeyError, IndexError, ValueError, TypeError) as exc:
        with transacao(p.con):
            p.con.execute("UPDATE ia_interacoes SET situacao = 'erro', erro = ?, atualizado_em = ? WHERE id = ?",
                          (str(exc), agora(), iid))
            registrar(p.con, "ia", iid, "erro_envio", origem="sistema", justificativa=str(exc))
        raise ErroValidacao(f"Falha na chamada à IA: {exc}") from exc
    with transacao(p.con):
        p.con.execute("UPDATE ia_interacoes SET situacao = 'respondida', resposta = ?, tokens_entrada = ?, tokens_saida = ?,"
                      " custo_estimado = ?, atualizado_em = ? WHERE id = ?",
                      (conteudo, te, ts, custo, agora(), iid))
        n = _criar_sugestoes(p, i, dados)
        registrar(p.con, "ia", iid, "receber_resposta", origem="ia",
                  depois={"tokens_entrada": te, "tokens_saida": ts, "custo_usd": round(custo, 6), "sugestoes": n,
                          "resposta": dados})
    return obter_interacao(p, iid)


def _json_da_resposta(conteudo: str) -> dict:
    """Interpreta a resposta como JSON (tolerando cercas de código ```json)."""
    texto = (conteudo or "").strip()
    try:
        return json.loads(texto)
    except ValueError:
        m = re.search(r"\{.*\}", texto, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# ------------------------------------------------------------------ validação da resposta
def _norm(t):
    return " ".join((t or "").split())


def _localizar(texto: str, citacao: str):
    """Posição exata da citação no texto (tolerando diferenças de espaço)."""
    if not citacao:
        return None
    i = texto.find(citacao)
    if i >= 0:
        return i, i + len(citacao)
    partes = [re.escape(x) for x in citacao.split()]
    if not partes:
        return None
    m = re.search(r"\s+".join(partes), texto)
    return (m.start(), m.end()) if m else None


def _criar_sugestoes(p, i, dados) -> int:
    op = i["operacao"]
    material = json.loads(i["corpo"]["messages"][1]["content"].split("MATERIAL (JSON):\n", 1)[1])
    registros = {r.get("id"): r for r in i["registros_obj"] if r.get("id")}
    enviados_frag = {int(r["fragmento_id"]) for r in i["registros_obj"] if r.get("tipo") == "fragmento"}
    criadas = 0

    def nova(tipo, conteudo, alertas):
        nonlocal criadas
        p.con.execute("INSERT INTO sugestoes(interacao_id, tipo, conteudo, alertas, criado_em) VALUES (?,?,?,?,?)",
                      (i["id"], tipo, json.dumps(conteudo, ensure_ascii=False), json.dumps(alertas, ensure_ascii=False), agora()))
        criadas += 1

    if op == "sugerir_codigos":
        passagens = {x["id"]: x for x in material["passagens"]}
        existentes = {c["id"] for c in material["codigos_existentes"]}
        for s in dados.get("sugestoes", []) if isinstance(dados.get("sugestoes"), list) else []:
            alertas, evs = [], []
            for e in s.get("evidencias", []) or []:
                pid = str(e.get("passagem", ""))
                if pid not in passagens:
                    alertas.append(f"Evidência descartada: passagem “{pid}” não foi enviada."); continue
                pos = _localizar(passagens[pid]["texto"], e.get("citacao", ""))
                if not pos:
                    alertas.append(f"Evidência descartada: a citação não existe literalmente em {pid}: “{_compactar(e.get('citacao', ''), 120)}”.")
                    continue
                reg = registros[pid]
                if reg["tipo"] == "passagem":
                    evs.append({"passagem": pid, "documento_id": reg["documento_id"], "inicio": reg["inicio"] + pos[0],
                                "fim": reg["inicio"] + pos[1], "citacao": passagens[pid]["texto"][pos[0]:pos[1]],
                                "participante": passagens[pid]["participante"]})
                else:
                    evs.append({"passagem": pid, "fragmento_id": reg["fragmento_id"], "citacao": passagens[pid]["texto"],
                                "participante": passagens[pid]["participante"]})
            ce = s.get("codigo_existente_id")
            if ce is not None and ce not in existentes:
                alertas.append(f"Código existente {ce} não foi enviado; tratado como código novo.")
                ce = None
            if not evs:
                alertas.append("Nenhuma evidência válida: esta sugestão não poderá ser aplicada a fragmentos.")
            nova("codigo", {"nome": _compactar(s.get("nome", ""), 120), "descricao": s.get("descricao", ""),
                            "justificativa": s.get("justificativa", ""), "codigo_existente_id": ce, "evidencias": evs}, alertas)
    elif op == "sugerir_agrupamentos":
        validos = {c["id"] for c in material["codigos"]}
        for g in dados.get("agrupamentos", []) or []:
            ids = [c for c in g.get("codigos", []) if isinstance(c, int)]
            inval = [c for c in ids if c not in validos]
            alertas = [f"Códigos ignorados (não enviados): {inval}"] if inval else []
            nova("agrupamento", {"tema": _compactar(g.get("tema", ""), 150), "descricao": g.get("descricao", ""),
                                 "codigos": [c for c in ids if c in validos], "justificativa": g.get("justificativa", "")}, alertas)
        for r in dados.get("relacoes", []) or []:
            a, b = r.get("codigo_a"), r.get("codigo_b")
            if a in validos and b in validos:
                nova("relacao", {"codigo_a": a, "codigo_b": b, "relacao": r.get("relacao", ""),
                                 "justificativa": r.get("justificativa", "")}, [])
    elif op == "sugerir_nomes_tema":
        tid = material["tema"]["id"]
        for n in dados.get("nomes", []) or []:
            nova("nome_tema", {"tema_id": tid, "nome": _compactar(n.get("nome", ""), 150), "justificativa": n.get("justificativa", "")}, [])
    elif op == "sintetizar_tema":
        tid = material["tema"]["id"]
        texto = dados.get("sintese", "") or ""
        refs = set(re.findall(r"\[F(\d+)\]", texto))
        invalidas = sorted(r for r in refs if int(r) not in enviados_frag)
        alertas = [f"Referências a fragmentos não enviados foram removidas: {', '.join('F' + r for r in invalidas)}"] if invalidas else []
        for r in invalidas:
            texto = texto.replace(f"[F{r}]", "[referência inválida removida]")
        texto = re.sub(r"\[F(\d+)\]", lambda m: f"[[frag:{m[1]}]]", texto)
        for trecho in re.findall(r"[“\"]([^”\"]{25,})[”\"]", texto):
            if not any(_norm(trecho) in _norm(f["texto"]) for f in material["fragmentos"]):
                alertas.append(f"Possível citação sem correspondência nos fragmentos: “{_compactar(trecho, 100)}”. Verifique.")
        nova("sintese", {"tema_id": tid, "texto": texto}, alertas)
    elif op in ("revisar_coerencia", "comparar_temas", "organizar_evidencias"):
        nova({"revisar_coerencia": "coerencia", "comparar_temas": "comparacao", "organizar_evidencias": "organizacao"}[op],
             {"tema_id": material.get("tema", {}).get("id"), "resposta": dados}, [])
    elif op == "verificar_privacidade":
        passagens = {x["id"]: x for x in material["passagens"]}
        for it in dados.get("possiveis_identificadores", []) or []:
            pid = str(it.get("passagem", "P1"))
            pos = _localizar(passagens.get(pid, {}).get("texto", ""), it.get("trecho", ""))
            alertas = [] if pos else ["O trecho apontado não foi encontrado literalmente no texto enviado."]
            reg = registros.get(pid, {})
            nova("privacidade", {"documento_id": reg.get("documento_id"), "trecho": it.get("trecho", ""),
                                 "motivo": it.get("motivo", ""),
                                 "inicio": reg.get("inicio", 0) + pos[0] if pos else None,
                                 "fim": reg.get("inicio", 0) + pos[1] if pos else None}, alertas)
    elif op == "revisar_texto":
        original = material["texto"]
        novo = dados.get("texto_revisado", "") or ""
        a, b = set(re.findall(r"\[\[frag:\d+\]\]", original)), set(re.findall(r"\[\[frag:\d+\]\]", novo))
        alertas = []
        if a != b:
            alertas.append("A revisão alterou as marcações de citação: " +
                           (f"removidas {sorted(a - b)} " if a - b else "") + (f"acrescentadas {sorted(b - a)}" if b - a else ""))
        reg = i["registros_obj"][0]
        nova("revisao_texto", {"relatorio_id": reg["relatorio_id"], "versao_base": reg["versao"], "texto": novo,
                               "comentarios": dados.get("comentarios", [])}, alertas)
    return criadas


# ------------------------------------------------------------------ sugestões
def listar_sugestoes(p: Projeto, *, situacao: str = "", interacao_id: int | None = None, tipo: str = "") -> list[dict]:
    cond, params = [], []
    if situacao:
        cond.append("s.situacao = ?"); params.append(situacao)
    if interacao_id:
        cond.append("s.interacao_id = ?"); params.append(interacao_id)
    if tipo:
        cond.append("s.tipo = ?"); params.append(tipo)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    out = linhas(p.con.execute(f"SELECT s.*, i.operacao, i.modelo FROM sugestoes s LEFT JOIN ia_interacoes i"
                               f" ON i.id = s.interacao_id {where} ORDER BY s.id DESC", params))
    for s in out:
        s["conteudo_obj"] = json.loads(s["conteudo"])
        s["alertas_obj"] = json.loads(s["alertas"] or "[]")
    return out


def obter_sugestao(p: Projeto, sid: int) -> dict:
    s = linha(p.con.execute("SELECT * FROM sugestoes WHERE id = ?", (sid,)).fetchone())
    if not s:
        raise ErroValidacao("Sugestão não encontrada.")
    s["conteudo_obj"] = json.loads(s["conteudo"])
    s["alertas_obj"] = json.loads(s["alertas"] or "[]")
    return s


def decidir_sugestao(p: Projeto, sid: int, decisao: str, *, editado: dict | None = None, observacao: str = "") -> dict:
    """Aceitar / aceitar com edição / rejeitar. Só aqui uma sugestão produz efeito."""
    from . import codificacao, notas, relatorios, temas
    s = obter_sugestao(p, sid)
    if s["situacao"] != "pendente":
        raise ErroValidacao("Esta sugestão já foi decidida.")
    if decisao not in ("aceita", "editada", "rejeitada"):
        raise ErroValidacao("Decisão inválida.")
    c = dict(s["conteudo_obj"])
    if decisao == "editada" and editado:
        c.update({k: v for k, v in editado.items() if v is not None})
    resultado = {}
    with transacao(p.con):
        if decisao != "rejeitada":
            t = s["tipo"]
            if t == "codigo":
                if c.get("codigo_existente_id"):
                    cid = int(c["codigo_existente_id"])
                else:
                    cid = codificacao.criar_codigo(p, c["nome"], c.get("descricao", ""),
                                                   (c.get("justificativa", "") + " [sugestão de IA aprovada]").strip(),
                                                   origem="ia")["id"]
                frags = []
                for e in c.get("evidencias", []):
                    if e.get("fragmento_id"):
                        fid = int(e["fragmento_id"])
                    else:
                        fid = codificacao.criar_fragmento(p, int(e["documento_id"]), int(e["inicio"]), int(e["fim"]),
                                                          texto_esperado=e["citacao"])["id"]
                    codificacao.codificar(p, fid, cid, "associação sugerida por IA e aprovada pelo pesquisador", origem="ia")
                    frags.append(fid)
                resultado = {"codigo_id": cid, "fragmentos": frags}
            elif t == "agrupamento":
                tema = temas.criar(p, c["tema"], c.get("descricao", ""),
                                   justificativa=(c.get("justificativa", "") + " [agrupamento sugerido por IA e aprovado]").strip(),
                                   origem="ia", codigo_ids=c.get("codigos", []))
                resultado = {"tema_id": tema["id"]}
            elif t == "nome_tema":
                temas.editar(p, int(c["tema_id"]), {"nome": c["nome"]}, motivo="nome sugerido por IA e aprovado")
                resultado = {"tema_id": c["tema_id"]}
            elif t == "sintese":
                rel = relatorios.anexar_texto(p, int(c["tema_id"]), c["texto"], origem="ia")
                resultado = {"relatorio_id": rel}
            elif t == "revisao_texto":
                v = relatorios.salvar_versao(p, int(c["relatorio_id"]), c["texto"], "Revisão textual sugerida por IA e aprovada",
                                             origem="ia")
                resultado = {"relatorio_versao": v}
            elif t in ("coerencia", "comparacao", "organizacao", "relacao", "privacidade"):
                texto = json.dumps(c.get("resposta", c), ensure_ascii=False, indent=1)
                nid = notas.criar(p, f"Sugestão de IA registrada como memorando (sugestão nº {sid}):\n{texto}",
                                  tipo="memo", titulo=f"IA — {t}", fase=4 if t != "privacidade" else None,
                                  entidade="tema" if c.get("tema_id") else "", entidade_id=c.get("tema_id"))["id"]
                resultado = {"nota_id": nid}
        p.con.execute("UPDATE sugestoes SET situacao = ?, decisao_observacao = ?, resultado = ?, decidido_em = ?,"
                      " conteudo = ? WHERE id = ?", (decisao, observacao, json.dumps(resultado), agora(),
                                                     json.dumps(c, ensure_ascii=False), sid))
        registrar(p.con, "sugestao", sid, {"aceita": "aceitar", "editada": "aceitar_com_edicao", "rejeitada": "rejeitar"}[decisao],
                  origem="pesquisador", antes={"conteudo": s["conteudo_obj"]},
                  depois={"conteudo": c, "resultado": resultado}, justificativa=observacao)
    return obter_sugestao(p, sid)
