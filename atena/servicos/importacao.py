"""Validação de arquivos e extração de texto (PDF, DOCX, ODT, RTF, TXT, MD, CSV, HTML, SRT, VTT)."""
from __future__ import annotations

import csv
import html
import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

from ..textos import normalizar

TAMANHO_MAXIMO = 50 * 1024 * 1024  # 50 MB

FORMATOS = {
    "txt": "Texto simples", "md": "Markdown", "csv": "Tabela CSV", "tsv": "Tabela TSV",
    "docx": "Word (DOCX)", "odt": "OpenDocument (ODT)", "rtf": "Rich Text (RTF)",
    "pdf": "PDF", "html": "Página HTML", "htm": "Página HTML",
    "srt": "Legenda/transcrição SRT", "vtt": "Legenda/transcrição WebVTT",
}


class ErroImportacao(ValueError):
    pass


def extensao(nome: str) -> str:
    return nome.rsplit(".", 1)[-1].lower() if "." in nome else ""


def validar(nome: str, dados: bytes) -> str:
    ext = extensao(nome)
    if ext not in FORMATOS:
        raise ErroImportacao(f"Formato .{ext or '?'} não suportado. Formatos aceitos: "
                             + ", ".join(sorted(FORMATOS)) + ".")
    if not dados:
        raise ErroImportacao("O arquivo está vazio.")
    if len(dados) > TAMANHO_MAXIMO:
        raise ErroImportacao("O arquivo excede 50 MB.")
    if ext == "pdf" and not dados[:1024].lstrip().startswith(b"%PDF"):
        raise ErroImportacao("O arquivo tem extensão .pdf, mas o conteúdo não é um PDF válido.")
    if ext in ("docx", "odt") and not zipfile.is_zipfile(io.BytesIO(dados)):
        raise ErroImportacao(f"O arquivo tem extensão .{ext}, mas não é um documento válido.")
    if ext == "rtf" and not dados.lstrip().startswith(b"{\\rtf"):
        raise ErroImportacao("O arquivo tem extensão .rtf, mas o conteúdo não é RTF.")
    if ext in ("txt", "md", "csv", "tsv", "srt", "vtt", "html", "htm") and b"\x00" in dados[:4096]:
        raise ErroImportacao("O arquivo parece ser binário, não texto.")
    return ext


def decodificar(dados: bytes) -> str:
    for cod in ("utf-8-sig", "utf-8"):
        try:
            return dados.decode(cod)
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes
        melhor = from_bytes(dados).best()
        if melhor is not None:
            return str(melhor)
    except Exception:
        pass
    return dados.decode("cp1252", errors="replace")


# ------------------------------------------------------------------ extratores
def _pdf(dados: bytes) -> tuple[str, list[str]]:
    avisos = []
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=dados, filetype="pdf") as doc:
            texto = "\n\n".join(pg.get_text("text") for pg in doc)
        metodo = "PyMuPDF"
    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(dados)) as pdf:
                texto = "\n\n".join((pg.extract_text() or "") for pg in pdf.pages)
            metodo = "pdfplumber"
        except ImportError:
            from pypdf import PdfReader
            leitor = PdfReader(io.BytesIO(dados))
            texto = "\n\n".join((pg.extract_text() or "") for pg in leitor.pages)
            metodo = "pypdf"
    if len(texto.strip()) < 20:
        avisos.append("Quase nenhum texto foi extraído do PDF. Ele pode ser digitalizado (imagem); "
                      "nesse caso é preciso aplicar OCR antes ou colar o texto manualmente.")
    avisos.append(f"Texto extraído com {metodo}. Confira quebras de linha e hifenizações.")
    return texto, avisos


def _docx(dados: bytes) -> tuple[str, list[str]]:
    import docx
    d = docx.Document(io.BytesIO(dados))
    partes = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            partes.append(" | ".join(c.text.strip() for c in row.cells))
    avisos = []
    if d.tables:
        avisos.append(f"{len(d.tables)} tabela(s) convertida(s) em linhas separadas por '|'.")
    return "\n".join(partes), avisos


def _odt(dados: bytes) -> tuple[str, list[str]]:
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        xml = z.read("content.xml")
    raiz = ElementTree.fromstring(xml)
    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    partes = []
    for el in raiz.iter():
        if el.tag in (f"{{{ns_text}}}p", f"{{{ns_text}}}h"):
            partes.append("".join(el.itertext()))
    return "\n".join(partes), []


def _rtf(dados: bytes) -> tuple[str, list[str]]:
    texto = dados.decode("latin-1", errors="replace")
    texto = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m[1], 16)]).decode("cp1252", "replace"), texto)
    texto = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m[1]) % 65536), texto)
    texto = re.sub(r"\{\\\*[^{}]*\}", "", texto)
    texto = re.sub(r"\{\\(fonttbl|colortbl|stylesheet|info)[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", texto)
    texto = re.sub(r"\\(par|line)\b ?", "\n", texto)
    texto = re.sub(r"\\tab\b ?", "\t", texto)
    texto = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", texto)
    texto = texto.replace("{", "").replace("}", "").replace("\\\\", "\\")
    return texto, ["RTF convertido de forma simplificada; confira a formatação."]


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes, self._ignorar = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignorar -= 1

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def _html(dados: bytes) -> tuple[str, list[str]]:
    p = _HTML()
    p.feed(decodificar(dados))
    return html.unescape("".join(p.partes)), []


_COL_LOCUTOR = ("locutor", "speaker", "falante", "participante", "nome")
_COL_TEXTO = ("texto", "text", "fala", "transcricao", "transcrição", "conteudo", "conteúdo", "content")
_COL_TEMPO = ("horario", "horário", "tempo", "time", "timestamp", "inicio", "início", "start")


def _csv(dados: bytes, sep: str | None = None) -> tuple[str, list[str]]:
    texto = decodificar(dados)
    if sep is None:
        try:
            sep = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t").delimiter
        except csv.Error:
            sep = ","
    linhas = list(csv.reader(io.StringIO(texto), delimiter=sep))
    if not linhas:
        return "", ["CSV vazio."]
    cab = [c.strip().lower() for c in linhas[0]]

    def achar(opcoes):
        for i, c in enumerate(cab):
            if c in opcoes:
                return i
        return None

    i_loc, i_txt, i_tmp = achar(_COL_LOCUTOR), achar(_COL_TEXTO), achar(_COL_TEMPO)
    if i_txt is not None:
        partes = []
        for row in linhas[1:]:
            if len(row) <= i_txt or not row[i_txt].strip():
                continue
            loc = row[i_loc].strip() if i_loc is not None and i_loc < len(row) else ""
            tmp = row[i_tmp].strip() if i_tmp is not None and i_tmp < len(row) else ""
            if loc:
                partes.append(f"{loc} - {tmp or '00:00'}:\n{row[i_txt].strip()}\n")
            else:
                partes.append(row[i_txt].strip() + "\n")
        return "\n".join(partes), ["CSV interpretado como transcrição (colunas de locutor/texto reconhecidas)."]
    partes = []
    for row in linhas[1:]:
        partes.append("; ".join(f"{linhas[0][k]}: {v}" for k, v in enumerate(row) if k < len(linhas[0]) and v.strip()))
    return "\n".join(partes), ["CSV convertido em texto: cada linha virou 'coluna: valor'."]


_TEMPO_SRT = re.compile(r"(\d{1,2}:\d{2}:\d{2})[,.]\d{1,3}\s*-->\s*\S+")


def _legenda(dados: bytes) -> tuple[str, list[str]]:
    texto = decodificar(dados).replace("\r\n", "\n")
    blocos, out = re.split(r"\n\s*\n", texto), []
    for b in blocos:
        linhas = [l for l in b.strip().split("\n") if l.strip() and l.strip() != "WEBVTT"]
        if not linhas:
            continue
        tempo = None
        resto = []
        for l in linhas:
            m = _TEMPO_SRT.search(l)
            if m:
                tempo = m[1]
            elif not l.strip().isdigit():
                resto.append(l.strip())
        if not resto:
            continue
        fala = " ".join(resto)
        m = re.match(r"^<v ([^>]+)>(.*)$", fala) or re.match(r"^([^:]{2,40}):\s+(.*)$", fala)
        loc, fala = (m[1].strip(), m[2].strip()) if m else ("NAO_IDENTIFICADO", fala)
        out.append(f"{loc} - {tempo or '00:00:00'}:\n{re.sub(r'<[^>]+>', '', fala)}\n")
    return "\n".join(out), ["Legenda convertida em turnos de fala (locutor - tempo)."]


def extrair(nome: str, dados: bytes) -> tuple[str, list[str]]:
    """Retorna (texto normalizado, avisos)."""
    ext = validar(nome, dados)
    try:
        if ext == "pdf":
            texto, avisos = _pdf(dados)
        elif ext == "docx":
            texto, avisos = _docx(dados)
        elif ext == "odt":
            texto, avisos = _odt(dados)
        elif ext == "rtf":
            texto, avisos = _rtf(dados)
        elif ext in ("html", "htm"):
            texto, avisos = _html(dados)
        elif ext == "csv":
            texto, avisos = _csv(dados)
        elif ext == "tsv":
            texto, avisos = _csv(dados, "\t")
        elif ext in ("srt", "vtt"):
            texto, avisos = _legenda(dados)
        else:
            texto, avisos = decodificar(dados), []
    except ErroImportacao:
        raise
    except Exception as exc:
        raise ErroImportacao(f"Não foi possível ler o arquivo ({type(exc).__name__}: {exc}).") from exc
    texto = normalizar(texto)
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{4,}", "\n\n\n", texto).strip() + "\n"
    if not texto.strip():
        raise ErroImportacao("Nenhum texto foi encontrado no arquivo.")
    return texto, avisos
