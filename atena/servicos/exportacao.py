"""Exportações em formatos abertos (CSV, XLSX, JSON, DOCX, PDF, Markdown, TXT).

As exportações comuns usam apenas a zona de trabalho (dados anonimizados).
O mapeamento de identidades só sai do ATENA pela exportação protegida, que
exige a senha do cofre, justificativa, e gera um arquivo cifrado com uma
senha de exportação.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

from .. import NOME, VERSAO, config
from ..auditoria import registrar
from ..cofre import _derivar, _params_kdf, cifrar_bytes, decifrar_bytes, validar_senha
from ..db import agora, linhas, transacao
from ..textos import sha256
from . import relatorios
from .projetos import ErroValidacao, Projeto

CONJUNTOS = {
    "participantes": "Cadastro de participantes (anonimizado)",
    "entrevistas": "Relação de entrevistas/documentos",
    "transcricoes": "Transcrições anonimizadas (versão em uso)",
    "diario": "Diário de familiarização e notas",
    "codigos": "Códigos e descrições",
    "fragmentos": "Fragmentos associados aos códigos",
    "temas": "Temas e subtemas",
    "evidencias": "Evidências por tema",
    "decisoes": "Decisões metodológicas e conclusões de fases",
    "historico": "Histórico completo de operações (auditoria)",
    "ia": "Registro de envios à IA e sugestões",
}


def _pasta(p: Projeto) -> Path:
    base = config.pasta_exportacoes() / re.sub(r"[^\w\-]+", "_", p.dados()["nome"])[:40]
    base.mkdir(parents=True, exist_ok=True)
    return base


def _carimbo():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ------------------------------------------------------------------ conjuntos tabulares
def tabela(p: Projeto, nome: str) -> tuple[list[str], list[list]]:
    if nome == "participantes":
        campos = json.loads(p.dados()["campos_participante"] or "[]")
        rs = linhas(p.con.execute("SELECT * FROM participantes WHERE ativo = 1 ORDER BY numero"))
        extras = list(dict.fromkeys(campos + [k for r in rs for k in json.loads(r["metadados"]).keys()]))
        cab = ["codigo", "situacao"] + extras + ["observacoes", "criado_em"]
        return cab, [[r["codigo"], r["situacao"]] + [json.loads(r["metadados"]).get(k, "") for k in extras]
                     + [r["observacoes"], r["criado_em"]] for r in rs]
    if nome == "entrevistas":
        rs = linhas(p.con.execute(
            "SELECT d.codigo, d.titulo, d.tipo, pa.codigo AS participante, d.situacao, d.data_coleta,"
            " (SELECT MAX(numero) FROM transcricao_versoes v WHERE v.documento_id = d.id) AS versao,"
            " (SELECT COUNT(*) FROM fragmentos f WHERE f.documento_id = d.id AND f.ativo = 1) AS fragmentos,"
            " d.sha256_original, d.criado_em FROM documentos d LEFT JOIN participantes pa ON pa.id = d.participante_id"
            " WHERE d.ativo = 1 ORDER BY d.codigo"))
        cab = ["codigo", "titulo", "tipo", "participante", "situacao", "data_coleta", "versao_anonimizada", "fragmentos",
               "sha256_original", "importado_em"]
        return cab, [list(r.values()) for r in rs]
    if nome == "transcricoes":
        rs = linhas(p.con.execute(
            "SELECT d.codigo, pa.codigo AS participante, v.numero, v.sha256, v.texto FROM documentos d"
            " JOIN transcricao_versoes v ON v.documento_id = d.id AND v.numero = (SELECT MAX(numero) FROM transcricao_versoes x"
            " WHERE x.documento_id = d.id) LEFT JOIN participantes pa ON pa.id = d.participante_id WHERE d.ativo = 1 ORDER BY d.codigo"))
        return ["documento", "participante", "versao", "sha256", "texto"], [list(r.values()) for r in rs]
    if nome == "diario":
        rs = linhas(p.con.execute("SELECT n.id, n.tipo, n.fase, n.titulo, n.texto, d.codigo AS documento, n.fragmento_id,"
                                  " n.criado_em, n.atualizado_em FROM notas n LEFT JOIN documentos d ON d.id = n.documento_id"
                                  " WHERE n.ativo = 1 ORDER BY n.criado_em"))
        return ["id", "tipo", "fase", "titulo", "texto", "documento", "fragmento", "criado_em", "atualizado_em"], [list(r.values()) for r in rs]
    if nome == "codigos":
        from .codificacao import listar_codigos
        rs = listar_codigos(p, situacao="")
        return (["id", "nome", "descricao", "justificativa", "origem", "situacao", "fragmentos", "documentos", "participantes",
                 "temas", "criado_em", "atualizado_em"],
                [[c["id"], c["nome"], c["descricao"], c["justificativa"], c["origem"], c["situacao"], c["n_fragmentos"],
                  c["n_documentos"], c["n_participantes"], "; ".join(t["nome"] for t in c["temas"]), c["criado_em"],
                  c["atualizado_em"]] for c in rs])
    if nome == "fragmentos":
        rs = linhas(p.con.execute(
            "SELECT f.id AS fragmento, c.nome AS codigo, d.codigo AS documento, pa.codigo AS participante, f.inicio, f.fim,"
            " f.texto, f.situacao, cf.origem, cf.justificativa, cf.criado_em FROM codificacoes cf"
            " JOIN fragmentos f ON f.id = cf.fragmento_id JOIN codigos c ON c.id = cf.codigo_id"
            " JOIN documentos d ON d.id = f.documento_id LEFT JOIN participantes pa ON pa.id = d.participante_id"
            " WHERE cf.ativo = 1 AND f.ativo = 1 AND c.situacao = 'ativo' ORDER BY c.nome, d.codigo, f.inicio"))
        return ["fragmento", "codigo", "documento", "participante", "inicio", "fim", "texto", "situacao_fragmento",
                "origem", "justificativa", "codificado_em"], [list(r.values()) for r in rs]
    if nome == "temas":
        from .temas import listar
        rs = listar(p, incluir_inativos=True)
        nomes = {t["id"]: t["nome"] for t in rs}
        out = []
        for t in rs:
            cods = [r[0] for r in p.con.execute("SELECT c.nome FROM tema_codigos tc JOIN codigos c ON c.id = tc.codigo_id"
                                                " WHERE tc.tema_id = ? AND tc.ativo = 1 AND c.situacao = 'ativo'", (t["id"],))]
            out.append([t["id"], t["nome"], "subtema" if t["pai_id"] else "tema", nomes.get(t["pai_id"], ""), t["situacao"],
                        t["descricao"], t["conceito_central"], t["escopo"], t["limites"], t["relacao_pergunta"],
                        t["justificativa"], "; ".join(cods), t.get("n_fragmentos", 0), t.get("n_participantes", 0),
                        t["definicao_confirmada_em"] or ""])
        return ["id", "nome", "nivel", "tema_pai", "situacao", "descricao", "conceito_central", "escopo", "limites",
                "relacao_pergunta", "justificativa", "codigos", "fragmentos", "participantes", "definicao_confirmada_em"], out
    if nome == "evidencias":
        from .temas import evidencias, listar
        out = []
        for t in listar(p):
            for f in evidencias(p, t["id"]):
                out.append([t["nome"], f["id"], f["participante_codigo"] or "", f["documento_codigo"],
                            "; ".join(c["nome"] for c in f["codigos"] if c["no_tema"]), "sim" if f["representativa"] else "",
                            f["texto"]])
        return ["tema", "fragmento", "participante", "documento", "codigos", "representativa", "texto"], out
    if nome == "decisoes":
        out = [["decisão", d["fase"] or "", d["titulo"], d["descricao"], d["justificativa"], d["criado_em"]]
               for d in linhas(p.con.execute("SELECT * FROM decisoes WHERE ativo = 1 ORDER BY id"))]
        out += [["fase " + h["operacao"], h["fase"], f"Fase {h['entidade_id']}", "", h["justificativa"], h["momento"]]
                for h in linhas(p.con.execute("SELECT * FROM historico WHERE entidade = 'fase' ORDER BY id"))]
        return ["tipo", "fase", "titulo", "descricao", "justificativa", "data"], out
    if nome == "historico":
        rs = linhas(p.con.execute("SELECT id, momento, entidade, entidade_id, operacao, fase, origem, justificativa, antes, depois,"
                                  " hash FROM historico ORDER BY id"))
        return ["id", "momento", "entidade", "entidade_id", "operacao", "fase", "origem", "justificativa", "antes", "depois",
                "hash"], [list(r.values()) for r in rs]
    if nome == "ia":
        rs = linhas(p.con.execute("SELECT i.id, i.criado_em, i.operacao, i.finalidade, i.modelo, i.situacao, i.confirmado_em,"
                                  " i.tokens_entrada, i.tokens_saida, i.custo_estimado, i.registros,"
                                  " (SELECT COUNT(*) FROM sugestoes s WHERE s.interacao_id = i.id AND s.situacao IN ('aceita','editada')) AS aceitas,"
                                  " (SELECT COUNT(*) FROM sugestoes s WHERE s.interacao_id = i.id AND s.situacao = 'rejeitada') AS rejeitadas,"
                                  " i.conteudo_enviado, i.resposta FROM ia_interacoes i ORDER BY i.id"))
        return ["id", "data", "operacao", "finalidade", "modelo", "situacao", "autorizado_em", "tokens_entrada", "tokens_saida",
                "custo_usd", "registros", "sugestoes_aceitas", "sugestoes_rejeitadas", "conteudo_enviado", "resposta"], \
            [list(r.values()) for r in rs]
    raise ErroValidacao("Conjunto de dados desconhecido.")


def _registrar(p, tipo, formato, caminho: Path, protegida=False):
    h = sha256(caminho.read_bytes())
    with transacao(p.con):
        p.con.execute("INSERT INTO exportacoes(tipo, formato, arquivo, protegida, sha256, criado_em) VALUES (?,?,?,?,?,?)",
                      (tipo, formato, caminho.name, 1 if protegida else 0, h, agora()))
        registrar(p.con, "exportacao", None, "exportar_protegido" if protegida else "exportar",
                  depois={"tipo": tipo, "formato": formato, "arquivo": caminho.name, "sha256": h})


def _csv_bytes(cab, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cab)
    w.writerows(rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM: o Excel abre com acentos corretos


def _xlsx(p, nomes: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "LEIA-ME"
    pr = p.dados()
    for linha_ in [[f"{NOME} {VERSAO} — exportação de dados anonimizados"], ["Projeto", pr["nome"]], ["Gerado em", agora()],
                   [], ["Conteúdo"]] + [[CONJUNTOS[n], n] for n in nomes] + \
                  [[], ["Observação", "Os participantes aparecem apenas por códigos. O mapeamento de identidades não faz parte desta exportação."],
                   ["Observação", "Frequências e contagens são descritivas e não indicam, por si, relevância temática."]]:
        ws.append(linha_)
    ws["A1"].font = Font(bold=True, size=13)
    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 80
    for n in nomes:
        cab, rows = tabela(p, n)
        s = wb.create_sheet(n[:31])
        s.append(cab)
        for r in rows:
            s.append([("" if v is None else (v if isinstance(v, (int, float)) else str(v)[:32000])) for v in r])
        for c in s[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="0F766E")
        s.freeze_panes = "A2"
        for i, col in enumerate(cab, 1):
            larg = max([len(str(col))] + [min(60, len(str(r[i - 1] or ""))) for r in rows[:200]]) + 2
            s.column_dimensions[s.cell(1, i).column_letter].width = min(70, larg)
            for c in s.iter_rows(min_row=2, min_col=i, max_col=i):
                c[0].alignment = Alignment(wrap_text=larg >= 60, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportar_conjunto(p: Projeto, nome: str, formato: str) -> Path:
    if nome not in CONJUNTOS and nome != "tudo":
        raise ErroValidacao("Conjunto inválido.")
    nomes = list(CONJUNTOS) if nome == "tudo" else [nome]
    destino = _pasta(p) / f"{nome}_{_carimbo()}.{formato}"
    if formato == "csv":
        if len(nomes) > 1:
            raise ErroValidacao("Para exportar todos os conjuntos em CSV, use o pacote ZIP.")
        destino.write_bytes(_csv_bytes(*tabela(p, nome)))
    elif formato == "xlsx":
        destino.write_bytes(_xlsx(p, nomes))
    elif formato == "json":
        dados = {"gerador": f"{NOME} {VERSAO}", "projeto": {k: v for k, v in p.dados().items() if k != "uid"},
                 "gerado_em": agora(), "conjuntos": {}}
        for n in nomes:
            cab, rows = tabela(p, n)
            dados["conjuntos"][n] = [dict(zip(cab, r)) for r in rows]
        destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif formato == "zip":
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for n in nomes:
                z.writestr(f"csv/{n}.csv", _csv_bytes(*tabela(p, n)))
            z.writestr("dados.xlsx", _xlsx(p, nomes))
            cab, rows = tabela(p, "transcricoes")
            for r in rows:
                z.writestr(f"transcricoes/{r[0]}_{r[1] or 'sem_participante'}_v{r[2]}.txt", r[4])
            z.writestr("LEIA-ME.txt", "Exportação do ATENA com dados anonimizados.\nO mapeamento de identidades NÃO está incluído.\n"
                                      "CSV: separador ';' e codificação UTF-8.\n")
    elif formato == "docx" and nome == "transcricoes":
        destino.write_bytes(_docx_transcricoes(p))
    else:
        raise ErroValidacao("Formato não disponível para este conjunto.")
    _registrar(p, nome, formato, destino)
    return destino


# ------------------------------------------------------------------ documentos (DOCX/PDF)
def _docx_base():
    import docx
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Cm, Pt
    d = docx.Document()
    for s in d.sections:
        s.top_margin, s.left_margin, s.bottom_margin, s.right_margin = Cm(3), Cm(3), Cm(2), Cm(2)
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    st.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    st.paragraph_format.space_after = Pt(6)
    for nome in ("Heading 1", "Heading 2", "Heading 3", "Title"):
        try:
            d.styles[nome].font.name = "Times New Roman"
        except KeyError:
            pass
    return d


def _runs(par, texto):
    for parte in re.split(r"(\*\*.+?\*\*|\*[^*]+?\*)", texto):
        if not parte:
            continue
        if parte.startswith("**") and parte.endswith("**"):
            par.add_run(parte[2:-2]).bold = True
        elif parte.startswith("*") and parte.endswith("*") and len(parte) > 2:
            par.add_run(parte[1:-1]).italic = True
        else:
            par.add_run(parte)


def docx_relatorio(p: Projeto, conteudo: str, titulo: str) -> bytes:
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.shared import Cm, Pt, RGBColor
    d = _docx_base()
    for b in relatorios.blocos(p, conteudo):
        t = b["tipo"]
        if t == "titulo":
            d.add_heading(relatorios._inline_token(p, b["texto"]), level=min(b["nivel"], 4) - 1)
        elif t == "paragrafo":
            par = d.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            par.paragraph_format.first_line_indent = Cm(1.25)
            _runs(par, relatorios._inline_token(p, b["texto"]))
        elif t == "destaque":
            par = d.add_paragraph()
            par.paragraph_format.left_indent = Cm(1)
            _runs(par, relatorios._inline_token(p, b["texto"]))
            for r in par.runs:
                r.italic = True
        elif t in ("lista", "numerada"):
            for x in b["itens"]:
                _runs(d.add_paragraph(style="List Bullet" if t == "lista" else "List Number"), relatorios._inline_token(p, x))
        elif t == "tabela" and b["linhas"]:
            ncol = max(len(r) for r in b["linhas"])
            tb = d.add_table(rows=0, cols=ncol)
            tb.style = "Table Grid"
            for k, r in enumerate(b["linhas"]):
                cells = tb.add_row().cells
                for j in range(ncol):
                    cells[j].text = ""
                    par = cells[j].paragraphs[0]
                    _runs(par, r[j] if j < len(r) else "")
                    for run in par.runs:
                        run.font.size = Pt(10)
                        if k == 0:
                            run.bold = True
            d.add_paragraph()
        elif t == "citacao":
            par = d.add_paragraph()
            par.paragraph_format.left_indent = Cm(4)  # citação longa (ABNT NBR 10520)
            par.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            if b["texto"] is None:
                par.add_run(f"[fragmento {b['fid']} inexistente]").font.color.rgb = RGBColor(0xB9, 0x1C, 0x1C)
            else:
                r = par.add_run(" ".join(b["texto"].split()))
                r.font.size = Pt(10)
                r2 = par.add_run(f" ({b['participante'] or '—'}, {b['documento']}, fragmento nº {b['fid']})")
                r2.font.size = Pt(10)
                r2.bold = True
        elif t == "separador":
            d.add_paragraph("—" * 20).alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.core_properties.title = titulo
    d.core_properties.author = p.dados()["responsavel"] or ""
    d.core_properties.comments = f"Gerado pelo {NOME} {VERSAO}"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _docx_transcricoes(p: Projeto) -> bytes:
    from docx.shared import Pt
    d = _docx_base()
    d.add_heading("Transcrições anonimizadas", 0)
    cab, rows = tabela(p, "transcricoes")
    for r in rows:
        d.add_heading(f"{r[0]} — {r[1] or 'sem participante'} (versão {r[2]})", 1)
        for bloco in r[4].split("\n"):
            par = d.add_paragraph(bloco)
            for run in par.runs:
                run.font.size = Pt(11)
        d.add_page_break()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _fontes_pdf():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidatos = [
        ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/timesi.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    ]
    for reg, neg, ita in candidatos:
        if all(os.path.isfile(x) for x in (reg, neg, ita)):
            try:
                pdfmetrics.registerFont(TTFont("AtenaR", reg))
                pdfmetrics.registerFont(TTFont("AtenaB", neg))
                pdfmetrics.registerFont(TTFont("AtenaI", ita))
                from reportlab.pdfbase.pdfmetrics import registerFontFamily
                registerFontFamily("AtenaR", normal="AtenaR", bold="AtenaB", italic="AtenaI", boldItalic="AtenaB")
                return "AtenaR", "AtenaB", "AtenaI"
            except Exception:
                continue
    return "Times-Roman", "Times-Bold", "Times-Italic"


def pdf_relatorio(p: Projeto, conteudo: str, titulo: str) -> bytes:
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    fr, fb, fi = _fontes_pdf()
    base = ParagraphStyle("base", fontName=fr, fontSize=11.5, leading=17, alignment=TA_JUSTIFY, firstLineIndent=1.25 * cm,
                          spaceAfter=6)
    cit = ParagraphStyle("cit", parent=base, fontSize=10, leading=12.5, leftIndent=4 * cm, firstLineIndent=0, spaceAfter=10)
    tit = {n: ParagraphStyle(f"h{n}", fontName=fb, fontSize={1: 16, 2: 13.5, 3: 12, 4: 11.5}[n], leading=20,
                             spaceBefore=12 if n > 1 else 0, spaceAfter=8, textColor=colors.HexColor("#134e4a"))
           for n in (1, 2, 3, 4)}
    cel = ParagraphStyle("cel", fontName=fr, fontSize=9, leading=11)

    def inl(t):
        t = escape(relatorios._inline_token(p, t))
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        return re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", t)

    hist = []
    for b in relatorios.blocos(p, conteudo):
        t = b["tipo"]
        if t == "titulo":
            hist.append(Paragraph(inl(b["texto"]), tit[min(4, b["nivel"])]))
        elif t == "paragrafo":
            hist.append(Paragraph(inl(b["texto"]), base))
        elif t == "destaque":
            hist.append(Paragraph(f"<i>{inl(b['texto'])}</i>", ParagraphStyle("d", parent=base, leftIndent=cm, firstLineIndent=0)))
        elif t in ("lista", "numerada"):
            hist.append(ListFlowable([ListItem(Paragraph(inl(x), ParagraphStyle("li", parent=base, firstLineIndent=0)))
                                      for x in b["itens"]], bulletType="bullet" if t == "lista" else "1"))
        elif t == "tabela" and b["linhas"]:
            ncol = max(len(r) for r in b["linhas"])
            dados = [[Paragraph(inl(r[j] if j < len(r) else ""), cel) for j in range(ncol)] for r in b["linhas"]]
            tb = Table(dados, repeatRows=1, colWidths=[(16 * cm) / ncol] * ncol)
            tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
                                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ccfbf1")),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            hist += [tb, Spacer(1, 8)]
        elif t == "citacao":
            if b["texto"] is None:
                hist.append(Paragraph(f"[fragmento {b['fid']} inexistente]", cit))
            else:
                hist.append(Paragraph(f"{escape(' '.join(b['texto'].split()))} <b>({escape(b['participante'] or '—')}, "
                                      f"{escape(b['documento'])}, fragmento nº {b['fid']})</b>", cit))
        elif t == "separador":
            hist.append(Spacer(1, 12))

    def rodape(c, doc):
        c.saveState()
        c.setFont(fr, 8)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(3 * cm, 1.2 * cm, f"{titulo} — gerado pelo {NOME} em {datetime.now():%d/%m/%Y}")
        c.drawRightString(19 * cm, 1.2 * cm, str(doc.page))
        c.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=3 * cm, rightMargin=2 * cm, topMargin=3 * cm, bottomMargin=2 * cm,
                      title=titulo, author=p.dados()["responsavel"] or NOME).build(hist, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()


def exportar_relatorio(p: Projeto, rid: int, formato: str) -> Path:
    r = relatorios.obter(p, rid)
    conteudo = r["versao"]["conteudo"]
    nome = re.sub(r"[^\w\-]+", "_", r["titulo"])[:50]
    destino = _pasta(p) / f"{nome}_v{r['versao']['numero']}_{_carimbo()}.{formato}"
    if formato == "docx":
        destino.write_bytes(docx_relatorio(p, conteudo, r["titulo"]))
    elif formato == "pdf":
        destino.write_bytes(pdf_relatorio(p, conteudo, r["titulo"]))
    elif formato == "md":
        destino.write_text(relatorios.texto_puro(p, conteudo), encoding="utf-8")
    elif formato == "html":
        corpo = relatorios.html_relatorio(p, conteudo)
        destino.write_text(f"<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>{r['titulo']}</title>"
                           "<style>body{font-family:Georgia,serif;max-width:780px;margin:40px auto;line-height:1.6;padding:0 16px}"
                           "blockquote{margin:1em 0 1em 3em;font-size:.92em;border-left:3px solid #0f766e;padding-left:1em}"
                           "blockquote footer{font-weight:bold;font-size:.85em}table{border-collapse:collapse}td,th{border:1px solid #999;"
                           f"padding:4px 8px}}</style><body>{corpo}</body></html>", encoding="utf-8")
    else:
        raise ErroValidacao("Formato inválido.")
    _registrar(p, f"relatorio:{r['tipo']}", formato, destino)
    return destino


# ------------------------------------------------------------------ exportação protegida
def exportar_mapeamento_protegido(p: Projeto, senha_cofre: str, senha_exportacao: str, justificativa: str) -> Path:
    from .. import cofre as cofre_mod
    from . import protegido
    if not justificativa.strip():
        raise ErroValidacao("Informe a justificativa da exportação de dados identificáveis.")
    validar_senha(senha_exportacao)
    if senha_exportacao == senha_cofre:
        raise ErroValidacao("Use uma senha de exportação diferente da senha do cofre.")
    cofre_mod.abrir(p.con_cofre, senha_cofre)  # reautenticação obrigatória
    ids = protegido.identidades(p, registrar_consulta=True, motivo=f"exportação protegida: {justificativa}")
    dados = json.dumps({"gerador": f"{NOME} {VERSAO}", "projeto": p.dados()["nome"], "gerado_em": agora(),
                        "aviso": "CONTÉM DADOS PESSOAIS IDENTIFICÁVEIS. Armazene com segurança e elimine quando não for mais necessário.",
                        "identidades": [{"codigo": i["codigo"], "categoria": i["categoria"], "valor": i["valor"],
                                         "variantes": i["variantes"]} for i in ids]}, ensure_ascii=False).encode("utf-8")
    sal = os.urandom(16)
    params = _params_kdf()
    chave = _derivar(senha_exportacao, sal, params)
    pacote = {"formato": "ATENA-SEGURO-1", "params": params, "sal": sal.hex(),
              "dados": cifrar_bytes(chave, dados, "exportacao-protegida").hex()}
    destino = _pasta(p) / f"mapeamento_PROTEGIDO_{_carimbo()}.atenaseg"
    destino.write_text(json.dumps(pacote), encoding="utf-8")
    _registrar(p, "mapeamento_identidades", "atenaseg", destino, protegida=True)
    with transacao(p.con):
        registrar(p.con, "cofre", None, "exportacao_protegida", justificativa=justificativa,
                  depois={"identidades": len(ids), "arquivo": destino.name})
    return destino


def abrir_pacote_protegido(conteudo: bytes, senha: str) -> dict:
    try:
        pacote = json.loads(conteudo.decode("utf-8"))
        assert pacote.get("formato") == "ATENA-SEGURO-1"
    except Exception as exc:
        raise ErroValidacao("Arquivo não é um pacote protegido do ATENA.") from exc
    chave = _derivar(senha, bytes.fromhex(pacote["sal"]), pacote["params"])
    try:
        return json.loads(decifrar_bytes(chave, bytes.fromhex(pacote["dados"]), "exportacao-protegida"))
    except Exception as exc:
        raise ErroValidacao("Senha incorreta ou arquivo corrompido.") from exc


def listar_exportacoes(p: Projeto) -> list[dict]:
    out = linhas(p.con.execute("SELECT * FROM exportacoes ORDER BY id DESC LIMIT 200"))
    pasta = _pasta(p)
    for e in out:
        e["existe"] = (pasta / e["arquivo"]).is_file()
    return out


def caminho_exportacao(p: Projeto, arquivo: str) -> Path:
    arq = (_pasta(p) / arquivo).resolve()
    if arq.parent != _pasta(p).resolve() or not arq.is_file():
        raise ErroValidacao("Arquivo não encontrado.")
    return arq
