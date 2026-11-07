"""As seis fases da análise temática (Braun e Clarke, 2006).

A conclusão de uma fase é sempre um ato do pesquisador, com observação
registrada. Indicadores numéricos são apenas apoio descritivo: o sistema não
considera uma fase concluída por ter atingido algum número.
"""
from __future__ import annotations

from ..auditoria import registrar
from ..db import agora, linha, linhas, transacao
from .projetos import ErroValidacao, Projeto

FASES = {
    1: {"nome": "Familiarização com os dados", "rota": "entrevistas",
        "descricao": "Transcrever (quando for o caso), ler e reler os dados, anotando ideias iniciais.",
        "orientacao": ["Ler integralmente cada transcrição, mais de uma vez, de forma ativa.",
                       "Registrar no diário impressões, ideias iniciais e padrões percebidos.",
                       "Marcar trechos que chamam a atenção, sem ainda codificá-los.",
                       "A familiarização não termina quando todos os documentos foram abertos: "
                       "é o pesquisador quem avalia se conhece os dados em profundidade."]},
    2: {"nome": "Geração de códigos iniciais", "rota": "codigos",
        "descricao": "Codificar sistematicamente características interessantes de todo o conjunto de dados.",
        "orientacao": ["Codificar todo o conjunto de dados, dando atenção equivalente a cada item.",
                       "Um trecho pode receber vários códigos; mantenha um pouco de contexto no fragmento.",
                       "Descreva cada código e registre por que ele foi criado.",
                       "Sugestões de IA só valem depois de aprovadas, editadas ou rejeitadas por você."]},
    3: {"nome": "Busca por temas", "rota": "mapa",
        "descricao": "Agrupar códigos em temas potenciais, reunindo todos os dados relevantes para cada um.",
        "orientacao": ["Explore relações entre códigos, entre temas e entre níveis (temas e subtemas).",
                       "Use o mapa temático para reorganizar agrupamentos e justifique cada decisão.",
                       "Mantenha uma área de códigos ainda não alocados — nada precisa ser descartado agora.",
                       "Temas não são simplesmente os códigos mais frequentes."]},
    4: {"nome": "Revisão dos temas", "rota": "revisao",
        "descricao": "Verificar se os temas funcionam em relação aos extratos codificados (nível 1) e ao conjunto "
                     "de dados (nível 2), gerando um mapa temático da análise.",
        "orientacao": ["Nível 1: leia todos os extratos de cada tema e avalie se formam um padrão coerente.",
                       "Nível 2: avalie se o tema reflete o conjunto dos dados; releia as entrevistas.",
                       "Verifique a distinção entre temas; funda, divida ou descarte quando necessário.",
                       "Os sinais apresentados pelo ATENA são pistas, não veredictos."]},
    5: {"nome": "Definição e nomeação dos temas", "rota": "definicao",
        "descricao": "Refinar as especificidades de cada tema e a história geral que a análise conta, "
                     "gerando definições e nomes claros.",
        "orientacao": ["Para cada tema, escreva o conceito central, o escopo e os limites.",
                       "Relacione o tema à pergunta de pesquisa e à história geral da análise.",
                       "Escolha evidências representativas e confirme a definição."]},
    6: {"nome": "Produção do relatório", "rota": "relatorios",
        "descricao": "Selecionar extratos vívidos e convincentes, relacionar a análise à pergunta de pesquisa e à "
                     "literatura, produzindo um relatório acadêmico.",
        "orientacao": ["Todo extrato citado deve estar vinculado ao fragmento original e ao participante anônimo.",
                       "O relatório deve ir além da descrição: apresente o argumento analítico.",
                       "Textos preliminares de IA precisam ser revisados e aprovados por você."]},
}

ROTULOS = {"nao_iniciada": "Não iniciada", "em_andamento": "Em andamento", "concluida": "Concluída pelo pesquisador"}


def listar(p: Projeto) -> list[dict]:
    out = []
    for r in linhas(p.con.execute("SELECT * FROM fases ORDER BY numero")):
        r.update(FASES[r["numero"]])
        r["situacao_rotulo"] = ROTULOS[r["situacao"]]
        r["indicadores"] = indicadores(p, r["numero"])
        r["historico"] = linhas(p.con.execute(
            "SELECT momento, operacao, justificativa FROM historico WHERE entidade = 'fase' AND entidade_id = ?"
            " ORDER BY id DESC LIMIT 10", (str(r["numero"]),)))
        out.append(r)
    return out


def obter(p: Projeto, n: int) -> dict:
    r = linha(p.con.execute("SELECT * FROM fases WHERE numero = ?", (n,)).fetchone())
    if not r:
        raise ErroValidacao("Fase inválida.")
    r.update(FASES[n])
    r["situacao_rotulo"] = ROTULOS[r["situacao"]]
    r["indicadores"] = indicadores(p, n)
    return r


def indicadores(p: Projeto, n: int) -> list[tuple[str, str]]:
    q = lambda sql, *a: p.con.execute(sql, a).fetchone()[0]
    docs = q("SELECT COUNT(*) FROM documentos WHERE ativo = 1 AND situacao = 'anonimizado'")
    if n == 1:
        lidos = q("SELECT COUNT(DISTINCT documento_id) FROM leituras WHERE tipo = 'leitura_integral'")
        return [("Entrevistas anonimizadas", str(docs)),
                ("Leituras integrais registradas", f"{lidos} de {docs} entrevistas"),
                ("Notas de familiarização", str(q("SELECT COUNT(*) FROM notas WHERE ativo = 1 AND fase = 1"))),
                ("Trechos marcados como relevantes", str(q("SELECT COUNT(*) FROM fragmentos WHERE ativo = 1 AND relevante = 1")))]
    if n == 2:
        sem = q("SELECT COUNT(*) FROM documentos d WHERE d.ativo = 1 AND d.situacao = 'anonimizado' AND NOT EXISTS"
                " (SELECT 1 FROM fragmentos f JOIN codificacoes cf ON cf.fragmento_id = f.id WHERE f.documento_id = d.id"
                " AND f.ativo = 1 AND cf.ativo = 1)")
        return [("Códigos ativos", str(q("SELECT COUNT(*) FROM codigos WHERE situacao = 'ativo'"))),
                ("Fragmentos codificados", str(q("SELECT COUNT(DISTINCT cf.fragmento_id) FROM codificacoes cf"
                                                 " JOIN fragmentos f ON f.id = cf.fragmento_id WHERE cf.ativo = 1 AND f.ativo = 1"))),
                ("Entrevistas ainda sem codificação", str(sem)),
                ("Sugestões de IA pendentes", str(q("SELECT COUNT(*) FROM sugestoes WHERE situacao = 'pendente' AND tipo = 'codigo'")))]
    if n == 3:
        from .temas import codigos_sem_tema
        return [("Temas e subtemas em construção", str(q("SELECT COUNT(*) FROM temas WHERE situacao IN ('candidato','em_revisao')"))),
                ("Códigos ainda sem tema", str(len(codigos_sem_tema(p))))]
    if n == 4:
        return [("Temas revisados", str(q("SELECT COUNT(*) FROM temas WHERE situacao IN ('revisado','definido')"))),
                ("Registros de revisão", str(q("SELECT COUNT(*) FROM revisoes_tema"))),
                ("Temas fundidos/descartados", str(q("SELECT COUNT(*) FROM temas WHERE situacao IN ('fundido','descartado')")))]
    if n == 5:
        return [("Temas com definição confirmada", str(q("SELECT COUNT(*) FROM temas WHERE situacao = 'definido'"))),
                ("Definições desatualizadas", str(q("SELECT COUNT(*) FROM temas WHERE definicao_desatualizada = 1 AND situacao = 'definido'")))]
    return [("Relatórios", str(q("SELECT COUNT(*) FROM relatorios WHERE ativo = 1"))),
            ("Relatórios aprovados", str(q("SELECT COUNT(*) FROM relatorios WHERE ativo = 1 AND situacao = 'aprovado'")))]


def iniciar(p: Projeto, n: int) -> None:
    f = obter(p, n)
    if f["situacao"] != "nao_iniciada":
        return
    with transacao(p.con):
        p.con.execute("UPDATE fases SET situacao = 'em_andamento', iniciada_em = ?, atualizado_em = ? WHERE numero = ?",
                      (agora(), agora(), n))
        registrar(p.con, "fase", n, "iniciar", fase=n, antes={"situacao": f["situacao"]}, depois={"situacao": "em_andamento"})


def concluir(p: Projeto, n: int, observacao: str) -> None:
    f = obter(p, n)
    if not observacao.strip():
        raise ErroValidacao("Registre por que você considera esta fase concluída (isso fará parte do relatório metodológico).")
    with transacao(p.con):
        p.con.execute("UPDATE fases SET situacao = 'concluida', concluida_em = ?, observacao = ?, iniciada_em = COALESCE(iniciada_em, ?),"
                      " atualizado_em = ? WHERE numero = ?", (agora(), observacao, agora(), agora(), n))
        registrar(p.con, "fase", n, "concluir", fase=n, antes={"situacao": f["situacao"]},
                  depois={"situacao": "concluida", "indicadores_no_momento": dict(f["indicadores"])},
                  justificativa=observacao)


def reabrir(p: Projeto, n: int, motivo: str) -> None:
    f = obter(p, n)
    if f["situacao"] != "concluida":
        raise ErroValidacao("A fase não está concluída.")
    if not motivo.strip():
        raise ErroValidacao("Informe por que a fase está sendo reaberta (a análise temática é recursiva).")
    with transacao(p.con):
        p.con.execute("UPDATE fases SET situacao = 'em_andamento', atualizado_em = ? WHERE numero = ?", (agora(), n))
        registrar(p.con, "fase", n, "reabrir", fase=n, antes={"situacao": "concluida", "observacao": f["observacao"]},
                  depois={"situacao": "em_andamento"}, justificativa=motivo)
