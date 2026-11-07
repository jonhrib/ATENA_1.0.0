from atena.servicos import pii


def cats(t, **kw):
    return {(c.categoria, c.trecho) for c in pii.detectar(t, usar_spacy=False, **kw)}


def test_padroes_fixos():
    t = ("Meu e-mail é joana.teste@exemplo.com.br, telefone (41) 99876-5432 ou 41 3333-4444. CPF 529.982.247-25. "
         "CNPJ 12.345.678/0001-95. CEP 80000-000. Moro na Rua das Acácias, 120. Minha matrícula é 2020123456. "
         "Site https://exemplo.org/perfil e @perfil_teste.")
    r = cats(t)
    for esperado in [("EMAIL", "joana.teste@exemplo.com.br"), ("TELEFONE", "(41) 99876-5432"), ("TELEFONE", "41 3333-4444"),
                     ("DOCUMENTO", "529.982.247-25"), ("DOCUMENTO", "12.345.678/0001-95"), ("ENDERECO", "80000-000"),
                     ("ENDERECO", "Rua das Acácias, 120"), ("MATRICULA", "2020123456"), ("LINK", "https://exemplo.org/perfil"),
                     ("LINK", "@perfil_teste")]:
        assert esperado in r, esperado


def test_cpf_invalido_tem_confianca_menor():
    c = [x for x in pii.detectar("CPF 123.456.789-00 aqui", usar_spacy=False) if x.categoria == "DOCUMENTO"]
    assert c and c[0].confianca == "media"


def test_nomes_e_falsos_positivos():
    t = "Então a professora Helena Barros ajudou. Ela ficou clara depois. Eu uso Python e WhatsApp."
    r = cats(t)
    assert ("PESSOA", "Helena Barros") in r
    assert not any(tr in ("Python", "WhatsApp", "Então", "Ela") for _, tr in r)


def test_rotulo_de_locutor():
    assert ("PESSOA", "Carlos Eduardo") in cats("Carlos Eduardo - 10:01 AM:\nOi.\n")
    assert not any(t == "ENTREVISTADOR" for _, t in cats("ENTREVISTADOR - 10:01:\nOi.\n"))


def test_termos_protegidos_nunca_marcados():
    t = "Participei da Pegasus Soluções Tecnológicas na UNESPAR com a Maria Souza."
    r = cats(t, protegidos=["Pegasus Soluções Tecnológicas", "UNESPAR"])
    assert ("PESSOA", "Maria Souza") in r
    assert not any("Pegasus" in tr or "UNESPAR" in tr for _, tr in r)


def test_excecoes_e_mapeamento():
    t = "O Zeca falou com a Joana. Joana voltou."
    r = pii.detectar(t, excecoes=["Zeca"], identidades=[("Joana", "PESSOA_007", "PESSOA")], usar_spacy=False)
    assert not any(c.trecho == "Zeca" for c in r)
    jo = [c for c in r if c.trecho == "Joana"]
    assert len(jo) == 2 and all(c.codigo == "PESSOA_007" and c.fonte == "mapeamento" for c in jo)


def test_mapeamento_ignora_acentos_e_maiusculas():
    r = pii.detectar("conversei com JOSÉ e com jose.", identidades=[("José", "PESSOA_001", "PESSOA")], usar_spacy=False)
    assert sum(1 for c in r if c.codigo == "PESSOA_001") == 2


def test_local_por_contexto():
    assert ("LOCAL", "Vila Serena") in cats("Moro em Vila Serena desde criança.")


def test_sem_sobreposicao():
    r = pii.detectar("Escreva para ana.lima@exemplo.com hoje", identidades=[("Ana", "PESSOA_001", "PESSOA")], usar_spacy=False)
    spans = sorted((c.inicio, c.fim) for c in r)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    assert any(c.categoria == "EMAIL" for c in r)
