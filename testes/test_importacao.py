import io
import zipfile

import pytest

from atena.servicos import documentos, importacao, protegido, transcricoes
from atena.servicos.importacao import ErroImportacao
from atena.textos import sha256

TEXTO = "Entrevistador - 10:00:\nComo foi o curso?\n\nParticipante Ficticio - 10:01:\nFoi difícil no começo, mas melhorou.\n"


def _docx(texto):
    import docx
    d = docx.Document()
    for l in texto.split("\n"):
        d.add_paragraph(l)
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "célula A", "célula B"
    b = io.BytesIO(); d.save(b); return b.getvalue()


def _pdf(texto):
    from reportlab.pdfgen import canvas
    b = io.BytesIO(); c = canvas.Canvas(b)
    y = 800
    for l in texto.split("\n"):
        c.drawString(50, y, l); y -= 16
    c.save(); return b.getvalue()


def _odt(texto):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        ps = "".join(f"<text:p>{l}</text:p>" for l in texto.split("\n"))
        z.writestr("content.xml", '<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                   f'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>{ps}</office:text></office:body></office:document-content>')
    return b.getvalue()


@pytest.mark.parametrize("nome,gerar", [
    ("e.txt", lambda t: t.encode("utf-8")),
    ("e_latin1.txt", lambda t: t.encode("cp1252")),
    ("e.md", lambda t: t.encode("utf-8")),
    ("e.docx", _docx),
    ("e.pdf", _pdf),
    ("e.odt", _odt),
    ("e.html", lambda t: ("<html><body>" + "".join(f"<p>{l}</p>" for l in t.split("\n")) + "<script>x()</script></body></html>").encode()),
    ("e.rtf", lambda t: ("{\\rtf1\\ansi " + t.replace("\n", "\\par ").replace("í", "\\'ed").replace("ç", "\\'e7") + "}").encode("latin-1")),
])
def test_extracao_formatos(nome, gerar):
    texto, avisos = importacao.extrair(nome, gerar(TEXTO))
    assert "curso" in texto and "melhorou" in texto
    assert "x()" not in texto


def test_csv_transcricao_e_srt():
    csv_ = "locutor;horario;texto\nEntrevistador;10:00;Pergunta?\nP1;10:01;Resposta longa.\n".encode()
    t, _ = importacao.extrair("t.csv", csv_)
    assert "P1 - 10:01:\nResposta longa." in t
    srt = "1\n00:00:01,000 --> 00:00:03,000\nAna: Olá a todos\n\n2\n00:00:04,000 --> 00:00:06,000\nBruno: Tudo bem?\n".encode()
    t, _ = importacao.extrair("t.srt", srt)
    assert "Ana - 00:00:01:\nOlá a todos" in t


@pytest.mark.parametrize("nome,dados", [("x.exe", b"MZ"), ("x.pdf", b"nao sou pdf"), ("x.docx", b"texto"), ("x.txt", b""),
                                        ("x.txt", b"\x00\x01\x02binario")])
def test_validacao_rejeita_arquivos_invalidos(nome, dados):
    with pytest.raises(ErroImportacao):
        importacao.extrair(nome, dados)


def test_importacao_preserva_original_cifrado(projeto):
    dados = _docx(TEXTO)
    d = documentos.importar(projeto, "Entrevista-Fulano-Real.docx", dados, titulo="Entrevista A")
    assert d["situacao"] == "importado" and d["versao_atual"] is None
    nome, original = protegido.ler_original(projeto, d["id"])
    assert original == dados and nome == "Entrevista-Fulano-Real.docx"
    assert d["sha256_original"] == sha256(dados)
    arq = next(projeto.pasta_protegida.glob("*.bin"))
    assert arq.read_bytes() != dados and b"Fulano" not in arq.read_bytes()
    # o nome original do arquivo (que pode identificar alguém) não vai para o banco de trabalho
    assert b"Fulano" not in (projeto.pasta / "projeto.sqlite").read_bytes()
    ext = protegido.ler_texto(projeto, d["id"], "extraido")
    rev = protegido.ler_texto(projeto, d["id"], "revisado")
    assert ext["texto"] == rev["texto"] and "melhorou" in ext["texto"]


def test_texto_colado_e_ja_anonimizado(projeto):
    d = documentos.criar_manual(projeto, "PARTICIPANTE_001 - 10:00:\nTexto já anonimizado.", ja_anonimizado=True)
    assert d["situacao"] == "anonimizado"
    assert transcricoes.versao_atual(projeto, d["id"])["texto"].startswith("PARTICIPANTE_001")


def test_revisao_protegida_versionada(projeto):
    d = documentos.criar_manual(projeto, TEXTO)
    documentos.salvar_revisao_protegida(projeto, d["id"], TEXTO.replace("difícil", "muito difícil"), "correção conferida com o áudio")
    vs = protegido.versoes_texto(projeto, d["id"])
    assert [(v["tipo"], v["numero"]) for v in vs] == [("extraido", 1), ("revisado", 1), ("revisado", 2)]
    assert "muito difícil" in protegido.ler_texto(projeto, d["id"])["texto"]
    assert "muito" not in protegido.ler_texto(projeto, d["id"], "revisado", 1)["texto"]
