"""Projeto de demonstração com dados 100% FICTÍCIOS.

Nomes, instituições, e-mails, telefones e documentos abaixo foram inventados.
O CPF usado não passa na validação de dígitos (não pertence a ninguém).
Os resultados produzidos com estes dados não são resultados de pesquisa.
"""
from __future__ import annotations

from . import documentos, participantes, projetos

SENHA_DEMO = "demonstracao-atena"

ENTREVISTAS = {
    "entrevista_ficticia_A.txt": """[DADOS FICTÍCIOS — DEMONSTRAÇÃO DO ATENA]
Pesquisadora Demo - 02:00 PM:
Obrigada por aceitar participar. Pode se apresentar como quiser.

Marina Quaresma - 02:01 PM:
Oi! Eu sou a Marina Quaresma, estou no terceiro ano de Ciência da Computação na Universidade do Vale Fictício. Moro em Vila Serena, longe do campus, então pego dois ônibus por dia.

Pesquisadora Demo - 02:03 PM:
Como tem sido a sua rotina no curso?

Marina Quaresma - 02:04 PM:
Puxada. No primeiro ano eu quase desisti, porque cálculo me derrubou e eu trabalhava à noite numa loja. Eu chegava em casa às onze e ainda tinha lista de exercício. Teve uma semana que eu pensei seriamente em trancar.

Pesquisadora Demo - 02:06 PM:
E o que fez você continuar?

Marina Quaresma - 02:07 PM:
A Fictícia Jr., a empresa júnior. Lá eu encontrei gente que estava passando pela mesma coisa. O Otávio Prates, que era diretor de projetos, me ajudou a organizar meus horários. Eu senti que fazia parte de alguma coisa, sabe? Antes eu me sentia sozinha na sala.

Pesquisadora Demo - 02:09 PM:
A empresa júnior também trouxe dificuldades?

Marina Quaresma - 02:10 PM:
Trouxe. Em época de entrega de projeto eu deixava de estudar para prova. Meu coeficiente caiu. É uma faca de dois gumes: me deu motivo para ficar, mas também me sobrecarregou.

Pesquisadora Demo - 02:12 PM:
Quer deixar algum contato para devolutiva?

Marina Quaresma - 02:12 PM:
Pode ser meu e-mail marina.quaresma@exemplo.com.br ou o telefone (41) 98888-1234.
""",
    "entrevista_ficticia_B.txt": """[DADOS FICTÍCIOS — DEMONSTRAÇÃO DO ATENA]
Pesquisadora Demo - 10:00 AM:
Vamos começar. Conte um pouco da sua trajetória.

Caio Andrade Lemos - 10:01 AM:
Sou o Caio, faço Engenharia de Software na Universidade do Vale Fictício, estou no segundo ano. Entrei pelo Sisu, vim do interior e moro numa república com mais quatro pessoas.

Pesquisadora Demo - 10:02 AM:
Você já pensou em sair do curso?

Caio Andrade Lemos - 10:03 AM:
Já, várias vezes. O problema maior foi dinheiro. A bolsa atrasou três meses e eu tive que pegar freela de site para pagar o aluguel. Aí faltava tempo para estudar e eu reprovei em duas matérias. Minha mãe, a dona Rosa Lemos, dizia para eu voltar para casa.

Pesquisadora Demo - 10:05 AM:
E como você lidou com isso?

Caio Andrade Lemos - 10:06 AM:
Na Fictícia Jr. eu consegui um projeto remunerado indiretamente, porque ganhei experiência para um estágio depois. E os colegas me puxavam para estudar junto. Sem esse grupo eu teria largado. A professora Helena Barros também foi muito compreensiva com os prazos.

Pesquisadora Demo - 10:08 AM:
Algo mais que você queira acrescentar?

Caio Andrade Lemos - 10:09 AM:
Que a universidade podia ter mais apoio para quem trabalha. Meu CPF ficou até bloqueado no sistema da bolsa, 123.456.789-00, por erro deles, e ninguém resolvia.
""",
    "entrevista_ficticia_C.txt": """[DADOS FICTÍCIOS — DEMONSTRAÇÃO DO ATENA]
Pesquisadora Demo - 04:30 PM:
Obrigada pelo seu tempo. Como você descreveria o seu curso?

Bianca Tavares - 04:31 PM:
Eu estou no quarto ano de Sistemas de Informação na Universidade do Vale Fictício. Hoje eu gosto, mas no começo eu me sentia uma impostora. Todo mundo parecia saber programar desde criança, menos eu.

Pesquisadora Demo - 04:33 PM:
Isso afetou a sua permanência?

Bianca Tavares - 04:34 PM:
Muito. Tive crises de ansiedade antes das provas e cheguei a procurar o atendimento psicológico do campus. Eu pensava que não era capaz. A minha matrícula é 2020123456 e eu lembro de olhar o histórico e chorar.

Pesquisadora Demo - 04:36 PM:
E a empresa júnior?

Bianca Tavares - 04:37 PM:
Entrei na Fictícia Jr. no segundo ano. Foi lá que eu percebi que sabia fazer coisas de verdade, entregar para um cliente real. Isso me deu confiança. Mas confesso que às vezes a cobrança da empresa júnior aumentava a ansiedade, principalmente perto do fim do semestre.

Pesquisadora Demo - 04:39 PM:
Para encerrar, algo que você mudaria?

Bianca Tavares - 04:40 PM:
Mais acolhimento para quem chega sem experiência. E menos competição entre colegas.
""",
}


def criar_projeto_demo() -> tuple[str, str]:
    uid, codigo = projetos.criar(
        {"nome": "Demonstração do ATENA (dados fictícios)",
         "descricao": "Projeto de demonstração com entrevistas INVENTADAS. Não representa nenhuma pesquisa real.",
         "objetivo": "Praticar o fluxo completo do ATENA: anonimização, codificação, temas e relatório.",
         "problema": "Como estudantes fictícios descrevem a relação entre a empresa júnior e a permanência no curso?",
         "pergunta": "Que experiências os estudantes associam à vontade de desistir ou de permanecer no curso?",
         "responsavel": "Usuário de demonstração"},
        SENHA_DEMO, termos_protegidos=["Universidade do Vale Fictício", "Fictícia Jr.", "Ciência da Computação",
                                       "Engenharia de Software", "Sistemas de Informação", "Sisu"])
    p = projetos.Projeto(uid)
    try:
        partes = []
        for nome, meta in (("Marina Quaresma", {"Curso": "Ciência da Computação", "Período/etapa": "3º ano", "Vínculo": "Membro da EJ"}),
                           ("Caio Andrade Lemos", {"Curso": "Engenharia de Software", "Período/etapa": "2º ano", "Vínculo": "Membro da EJ"}),
                           ("Bianca Tavares", {"Curso": "Sistemas de Informação", "Período/etapa": "4º ano", "Vínculo": "Membro da EJ"})):
            variantes = [nome.split()[0]]
            partes.append(participantes.criar(p, meta, "realizada", "participante fictício", nome_real=nome, variantes=variantes))
        from . import protegido
        protegido.criar_identidade(p, "ENTREVISTADOR", "Pesquisadora Demo", [])
        for (arq, texto), part in zip(ENTREVISTAS.items(), partes):
            documentos.importar(p, arq, texto.encode("utf-8"), titulo=f"Entrevista fictícia {part['codigo'][-3:]}",
                                participante_id=part["id"], data_coleta="2026-01-15")
    finally:
        p.fechar()
    return uid, codigo
