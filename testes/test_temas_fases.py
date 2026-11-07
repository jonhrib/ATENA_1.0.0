import pytest

from atena.servicos import codificacao as cod, documentos, fases, notas, temas as tm, transcricoes as tr
from atena.servicos.projetos import ErroValidacao

TEXTO = ("PARTICIPANTE_001 - 10:01:\nA empresa júnior me fez sentir parte de algo. Mas a cobrança aumentava a ansiedade. "
         "Trabalhar à noite atrapalhava. A bolsa atrasou.\n")


@pytest.fixture
def base(projeto):
    d = documentos.criar_manual(projeto, TEXTO, ja_anonimizado=True)
    t = tr.versao_atual(projeto, d["id"])["texto"]
    ids = {}
    for trecho, nome in [("me fez sentir parte de algo", "Pertencimento"), ("a cobrança aumentava a ansiedade", "Sobrecarga"),
                         ("Trabalhar à noite atrapalhava", "Conciliar trabalho"), ("A bolsa atrasou", "Dificuldade financeira")]:
        i = t.index(trecho)
        f = cod.codificar_selecao(projeto, d["id"], i, i + len(trecho), novo_codigo={"nome": nome})
        ids[nome] = (f["codigos"][0]["id"], f["id"])
    return projeto, ids


def test_ciclo_completo_do_tema(base):
    p, ids = base
    t = tm.criar(p, "EJ ambígua", "", codigo_ids=[ids["Pertencimento"][0], ids["Sobrecarga"][0]], justificativa="mesmo espaço")
    assert t["situacao"] == "candidato" and len(t["codigos"]) == 2
    assert {c["nome"] for c in tm.codigos_sem_tema(p)} == {"Conciliar trabalho", "Dificuldade financeira"}
    with pytest.raises(ErroValidacao):
        tm.definir(p, t["id"], {"conceito_central": "x", "descricao": "y"}, confirmar=True)  # falta evidência
    tm.revisar(p, t["id"], 1, coerencia="coerente", decisao="manter")
    assert tm.obter(p, t["id"])["situacao"] == "em_revisao"
    tm.revisar(p, t["id"], 2, coerencia="faz sentido no conjunto", decisao="manter")
    assert tm.obter(p, t["id"])["situacao"] == "revisado"
    tm.marcar_evidencia(p, t["id"], ids["Pertencimento"][1])
    r = tm.definir(p, t["id"], {"nome": "A empresa júnior como espaço ambíguo", "conceito_central": "pertencimento e sobrecarga",
                                "descricao": "desc", "escopo": "e", "limites": "l", "relacao_pergunta": "r", "justificativa": "j"},
                   confirmar=True)
    assert r["tema"]["situacao"] == "definido" and r["tema"]["definicao_confirmada_em"]
    tm.atribuir_codigo(p, t["id"], ids["Conciliar trabalho"][0], "teste")
    assert tm.obter(p, t["id"])["definicao_desatualizada"] == 1


def test_subtema_fusao_divisao_descarte(base):
    p, ids = base
    a = tm.criar(p, "Condições materiais", codigo_ids=[ids["Conciliar trabalho"][0], ids["Dificuldade financeira"][0]])
    b = tm.criar(p, "Financeiro", codigo_ids=[ids["Dificuldade financeira"][0]])
    sub = tm.criar(p, "Trabalho", pai_id=a["id"])
    with pytest.raises(ErroValidacao):
        tm.criar(p, "neto", pai_id=sub["id"])
    diag = tm.diagnostico(p)
    assert any({s["a"]["id"], s["b"]["id"]} == {a["id"], b["id"]} for s in diag["sobreposicoes"])
    tm.fundir(p, [b["id"]], a["id"], "redundante")
    assert tm.obter(p, b["id"])["situacao"] == "fundido"
    novo = tm.dividir(p, a["id"], "Bolsas", [ids["Dificuldade financeira"][0]], "distinto")
    assert [c["nome"] for c in novo["codigos"]] == ["Dificuldade financeira"]
    assert [c["nome"] for c in tm.obter(p, a["id"])["codigos"]] == ["Conciliar trabalho"]
    with pytest.raises(ErroValidacao):
        tm.alterar_situacao(p, novo["id"], "descartado", "")
    tm.alterar_situacao(p, novo["id"], "descartado", "sem sustentação")
    tm.reativar(p, novo["id"], "reconsiderado")
    assert tm.obter(p, novo["id"])["situacao"] == "em_revisao"


def test_evidencias_tema_rastreaveis(base):
    p, ids = base
    t = tm.criar(p, "T", codigo_ids=[ids["Sobrecarga"][0]])
    sub = tm.criar(p, "S", pai_id=t["id"], codigo_ids=[ids["Pertencimento"][0]])
    ev = tm.evidencias(p, t["id"])
    assert {e["texto"] for e in ev} == {"a cobrança aumentava a ansiedade", "me fez sentir parte de algo"}
    assert all(e["participante_codigo"] is None and e["documento_codigo"] == "DOC_001" for e in ev)
    assert sum(d["n"] for d in tm.confronto_documentos(p, t["id"])) == 2


def test_fases_concluir_e_reabrir(projeto):
    assert fases.obter(projeto, 1)["situacao"] == "nao_iniciada"
    notas.criar(projeto, "Primeiras impressões", tipo="familiarizacao")
    assert fases.obter(projeto, 1)["situacao"] == "em_andamento"   # iniciar não conclui
    with pytest.raises(ErroValidacao):
        fases.concluir(projeto, 1, "")
    fases.concluir(projeto, 1, "Li todas as entrevistas duas vezes.")
    assert fases.obter(projeto, 1)["situacao"] == "concluida"
    fases.reabrir(projeto, 1, "nova entrevista")
    assert fases.obter(projeto, 1)["situacao"] == "em_andamento"
    ops = [r[0] for r in projeto.con.execute("SELECT operacao FROM historico WHERE entidade='fase'")]
    assert ops == ["concluir", "reabrir"]


def test_notas_e_decisoes_preservam_versoes(projeto):
    n = notas.criar(projeto, "texto 1", tipo="memo")
    notas.editar(projeto, n["id"], "texto 2", motivo="refino")
    h = projeto.con.execute("SELECT antes, depois FROM historico WHERE entidade='nota' AND operacao='editar'").fetchone()
    assert "texto 1" in h[0] and "texto 2" in h[1]
    did = notas.registrar_decisao(projeto, "Codificar em nível semântico", "d", "justificativa", 2)
    assert notas.decisoes(projeto)[0]["id"] == did
