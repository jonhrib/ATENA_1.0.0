"""Importação do sistema anterior, com uma pasta FICTÍCIA no mesmo formato."""
import io
import json
import zipfile

from cryptography.fernet import Fernet

from atena.servicos import documentos, legado, participantes, projetos, protegido, transcricoes


def _zip_legado(chave):
    mapa = {"entrevistas": {"Entrevista-Ficticia-A.txt": {"participante": "Pessoa Ficticia Alfa", "codigo": "PARTICIPANTE_003",
                                                          "entrevistador": "Pesquisador Ficticio", "pessoas": {"Professor Ficticio": "PESSOA_004"}}}}
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        base = "Desenvolvimento/"
        z.writestr(base + "transcricoes/processadas/PARTICIPANTE_003.txt",
                   "ENTREVISTADOR - 03:09 PM:\nComo foi?\n\nPARTICIPANTE_003 - 03:10 PM:\nO PESSOA_004 me ajudou na Pegasus.\n")
        z.writestr(base + "transcricoes/brutas/Entrevista-Ficticia-A.txt", "Pessoa Ficticia Alfa - 03:10 PM:\nnão deve ser importado\n")
        z.writestr(base + "dados/participantes.csv", "codigo,universidade,curso,municipio,empresa_junior,vinculo_ej,situacao_academica,etapa_curso,perfil,observacoes\n"
                                                     "PARTICIPANTE_003,UNESPAR,Computação,Apucarana,Pegasus,Membro atual,Cursando,3º ano,,\n")
        z.writestr(base + "dados/entidades_preservar.txt", "# comentário\nUNESPAR\nPegasus\n")
        z.writestr(base + "transcricoes/mapeamentos/mapeamentos.enc", Fernet(chave).encrypt(json.dumps(mapa).encode()))
    return b.getvalue()


def test_importa_transcricoes_ficha_termos_e_mapeamento(projeto):
    chave = Fernet.generate_key()
    z = _zip_legado(chave)
    assert legado.analisar(zip_bytes=z)["transcricoes"] == ["PARTICIPANTE_003.txt"]
    r = legado.importar(projeto, zip_bytes=z, chave_fernet=chave)
    assert r["documentos"] == 1 and r["participantes"] == 1 and r["identidades"] >= 2
    pa = participantes.por_codigo(projeto, "PARTICIPANTE_003")
    assert pa["metadados_obj"]["Instituição"] == "UNESPAR"
    d = documentos.listar(projeto)[0]
    assert d["situacao"] == "anonimizado" and d["participante_id"] == pa["id"]
    assert "PESSOA_004" in transcricoes.versao_atual(projeto, d["id"])["texto"]
    assert {t["termo"] for t in projetos.listar_termos(projeto, "protegido")} >= {"UNESPAR", "Pegasus"}
    ids = {i["codigo"]: i["valor"] for i in protegido.identidades(projeto)}
    assert ids["PARTICIPANTE_003"] == "Pessoa Ficticia Alfa" and ids["PESSOA_004"] == "Professor Ficticio"
    assert ids["ENTREVISTADOR"] == "Pesquisador Ficticio"
    # novos códigos continuam a numeração sem colidir
    assert protegido.criar_identidade(projeto, "PESSOA", "Nova Pessoa")["codigo"] == "PESSOA_005"
    assert documentos.listar(projeto).__len__() == 1  # brutas não foram importadas
