"""Identificação de possíveis informações pessoais (detecção local, sem internet).

Combina, em ordem de prioridade:

1. **Mapeamento do projeto** — valores e variantes já registrados no cofre
   (participantes, pessoas, locais etc.). Confiança alta.
2. **Padrões fixos** — e-mail, telefone, CPF (com dígito verificador), CNPJ, RG,
   CEP, endereços, matrículas/RA, links, @perfis, datas. Confiança alta/média.
3. **Rótulos de locutor** — nomes no formato ``Nome - hh:mm:``.
4. **Heurística de nomes próprios** — sequências com inicial maiúscula, pontuadas
   por uma lista de prenomes/sobrenomes frequentes no Brasil.
5. **spaCy** (opcional, se instalado com modelo em português).

Termos protegidos do projeto (por exemplo, nomes das Empresas Juniores e das
IES) nunca são marcados. Exceções configuradas pelo pesquisador não são
marcadas pela heurística.

A detecção automática **não é perfeita**: produz falsos positivos e pode
deixar de identificar dados pessoais. Por isso nenhuma substituição é feita
sem a conferência humana na tela de revisão.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from functools import lru_cache

from .. import config
from ..textos import dobrar, rotulos_locutor

AVISO = ("A identificação automática pode gerar falsos positivos e pode deixar de reconhecer "
         "informações pessoais (apelidos, descrições indiretas, nomes grafados de forma incomum, "
         "cargos únicos, eventos que identificam alguém). Leia o texto completo antes de aprovar.")

PRIORIDADE = {"mapeamento": 0, "manual": 0, "padrao": 1, "rotulo": 2, "contexto": 3, "spacy": 4, "heuristica": 5, "ia": 6}

LETRA_M = "A-ZÀ-ÖØ-Ý"
LETRA_m = "a-zß-öø-ÿ"
PALAVRA_NOME = rf"[{LETRA_M}][{LETRA_m}]+(?:-[{LETRA_M}]?[{LETRA_m}]+)?"
PARTICULA = r"(?:d[aeo]s?|von|van|del|di)"
SEQ_NOME = re.compile(rf"(?<![\w{LETRA_m}{LETRA_M}]){PALAVRA_NOME}(?:\s+(?:{PARTICULA}\s+)?{PALAVRA_NOME}){{0,4}}(?![\w])")
CODIGO_ANON = re.compile(r"\b(?:[A-Z]+_\d{3,}|ENTREVISTADOR[A]?|NAO_IDENTIFICADO)\b")


def _cpf_valido(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(d[i] * ((n + 1) - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != d[n]:
            return False
    return True


PADROES = [
    ("EMAIL", "alta", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None),
    ("LINK", "alta", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I), None),
    ("DOCUMENTO", "alta", re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)"), None),  # CNPJ
    ("DOCUMENTO", "alta", re.compile(r"(?<![\d.])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d])"), _cpf_valido),
    ("DOCUMENTO", "media", re.compile(r"(?<![\d.])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\d])"), None),  # formato de CPF
    ("DOCUMENTO", "alta", re.compile(r"\b(?:RG|R\.G\.|identidade)\s*(?:n[º°o.]*\s*)?(?:é\s*)?[:\-]?\s*(?P<v>\d{1,2}\.?\d{3}\.?\d{3}-?[\dxX])\b", re.I), None),
    ("TELEFONE", "alta", re.compile(r"(?<![\d])(?:\+?55[\s-]?)?\(?\d{2}\)?[\s-]?9?\d{4}[\s-]\d{4}(?![\d])"), None),
    ("TELEFONE", "alta", re.compile(r"(?<![\d])(?:\+?55\s?)?\(?\d{2}\)?\s?9\d{8}(?![\d])"), None),
    ("ENDERECO", "alta", re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)"), None),  # CEP
    ("ENDERECO", "alta", re.compile(
        rf"\b(?:Rua|R\.|Avenida|Av\.|Travessa|Tv\.|Alameda|Rodovia|Praça|Estrada|Largo)\s+"
        rf"(?:d[aeo]s?\s+)?[{LETRA_M}0-9][\w{LETRA_m}.]*(?:\s+(?:d[aeo]s?\s+)?[{LETRA_M}0-9][\w{LETRA_m}.]*){{0,5}}"
        rf"(?:,?\s*(?:n[º°o]\.?\s*)?\d{{1,5}})?"), None),
    ("MATRICULA", "alta", re.compile(
        r"\b(?:matr[íi]cula|RA|R\.A\.|registro acad[êe]mico|SIAPE|prontu[áa]rio|c[óo]digo de aluno)"
        r"\s*(?:n[º°o.]*\s*)?(?:é\s*)?(?:de\s*)?[:\-]?\s*(?P<v>[A-Za-z]{0,3}\d[\d.\-/]{3,})", re.I), None),
    ("LINK", "media", re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{3,}"), None),
    ("DATA", "baixa", re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?!\d)"), None),
    ("OUTRO", "baixa", re.compile(r"(?<![\d.,:/])\d{7,}(?![\d.,:/])"), None),
]

CONTEXTO_LOCAL = re.compile(
    rf"\b(?i:moro|morava|mora|moramos|nasci|cresci|vim|sou|é|cidade|bairro|município|interior)\s+"
    rf"(?i:em|no|na|de|do|da|lá\s+em|aqui\s+em)\s+({PALAVRA_NOME}(?:\s+(?:d[aeo]s?\s+)?{PALAVRA_NOME}){{0,3}})")


@dataclass
class Candidato:
    inicio: int
    fim: int
    trecho: str
    categoria: str
    fonte: str
    confianca: str
    codigo: str | None = None
    motivo: str = ""

    def dict(self):
        return asdict(self)


def nomes_comuns() -> frozenset:
    return _lista_n("nomes_comuns.txt")


def nao_nomes() -> frozenset:
    return _lista_n("nao_nomes.txt")


_cache_listas: dict[str, frozenset] = {}


def _lista_n(nome: str) -> frozenset:
    if nome not in _cache_listas:
        arq = config.pasta_recursos() / nome
        itens = set()
        if arq.is_file():
            for l in arq.read_text(encoding="utf-8").splitlines():
                l = l.split("#", 1)[0].strip()
                if l:
                    itens.add(dobrar(l))
        _cache_listas[nome] = frozenset(itens)
    return _cache_listas[nome]


def _ocorrencias(texto_dobrado: str, termo: str):
    """Posições de ``termo`` como palavra inteira, sem diferenciar maiúsculas/acentos."""
    t = dobrar(" ".join(termo.split()))
    if not t:
        return
    padrao = re.compile(r"(?<![\w])" + re.escape(t).replace(r"\ ", r"\s+") + r"(?![\w])")
    for m in padrao.finditer(texto_dobrado):
        yield m.start(), m.end()


def _sobrepoe(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def _inicio_de_frase(texto: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and texto[i] in " \t\"'“(«-—":
        i -= 1
    return i < 0 or texto[i] in ".!?\n:…"


def _spacy(texto: str):
    try:
        import spacy  # noqa: F401
    except ImportError:
        return []
    try:
        nlp = _modelo_spacy()
    except Exception:
        return []
    out = []
    for e in nlp(texto).ents:
        if e.label_ in ("PER", "LOC"):
            out.append(Candidato(e.start_char, e.end_char, e.text, "PESSOA" if e.label_ == "PER" else "LOCAL",
                                 "spacy", "media", motivo=f"spaCy: entidade {e.label_}"))
    return out


@lru_cache(maxsize=1)
def _modelo_spacy():
    import spacy
    for nome in ("pt_core_news_lg", "pt_core_news_md", "pt_core_news_sm"):
        try:
            return spacy.load(nome, disable=["lemmatizer", "parser"])
        except OSError:
            continue
    raise OSError("modelo spaCy em português não instalado")


def detectar(texto: str, *, protegidos=(), excecoes=(), identidades=(), usar_spacy: bool = True,
             incluir_baixa: bool = True) -> list[Candidato]:
    """Retorna candidatos ordenados por posição, sem sobreposição.

    ``identidades``: iterável de (valor, codigo, categoria).
    """
    dob = dobrar(texto)
    protegidos_pos = []
    for t in protegidos:
        protegidos_pos += list(_ocorrencias(dob, t))
    excecoes_n = {dobrar(" ".join(e.split())) for e in excecoes}
    nn = nao_nomes()
    nc = nomes_comuns()
    cand: list[Candidato] = []

    # 1. mapeamento
    for valor, codigo, categoria in identidades:
        for a, b in _ocorrencias(dob, valor):
            cand.append(Candidato(a, b, texto[a:b], categoria, "mapeamento", "alta", codigo,
                                  f"Já registrado no cofre como {codigo}"))
    # 2. padrões
    for categoria, conf, rx, valida in PADROES:
        for m in rx.finditer(texto):
            g = "v" if "v" in rx.groupindex else 0
            s = m.group(g).rstrip(".,;:)")
            if valida and not valida(s):
                continue
            cand.append(Candidato(m.start(g), m.start(g) + len(s), s, categoria, "padrao", conf,
                                  motivo=f"Padrão de {categoria.lower()}"))
    # 3. rótulos de locutor
    for a, b, nome in rotulos_locutor(texto):
        if CODIGO_ANON.fullmatch(nome.strip()) or dobrar(nome.strip()) in excecoes_n:
            continue
        cand.append(Candidato(a, b, nome, "PESSOA", "rotulo", "alta", motivo="Nome no rótulo de locutor"))
    # 4. locais por contexto
    for m in CONTEXTO_LOCAL.finditer(texto):
        a, b = m.start(1), m.end(1)
        if dobrar(m.group(1)) in nn or dobrar(m.group(1)) in excecoes_n:
            continue
        cand.append(Candidato(a, b, m.group(1), "LOCAL", "contexto", "media", motivo="Local mencionado após 'moro em', 'sou de' etc."))
    # 5. heurística de nomes
    for m in SEQ_NOME.finditer(texto):
        trecho = m.group(0)
        if CODIGO_ANON.search(trecho):
            continue
        partes = trecho.split()
        # descarta palavras iniciais que não são nomes ("Então Maria" → "Maria")
        desloc = 0
        while partes and dobrar(partes[0]) in nn:
            desloc += len(partes[0]) + 1
            partes = partes[1:]
        while partes and dobrar(partes[-1]) in nn | {"da", "de", "do", "das", "dos", "e"}:
            partes = partes[:-1]
        if not partes:
            continue
        ini = m.start() + desloc
        while texto[ini].isspace():
            ini += 1
        nome = " ".join(partes)
        fim = ini + len(nome)
        if texto[ini:fim] != nome:  # havia espaços múltiplos: recalcula pelo texto
            sub = re.search(re.escape(partes[0]) + r".*?" + re.escape(partes[-1]), texto[ini:m.end()])
            if not sub:
                continue
            fim = ini + sub.end()
            nome = texto[ini:fim]
        chave = dobrar(nome)
        if chave in excecoes_n or chave in nn:
            continue
        nomes_lista = [w for w in partes if dobrar(w) in nc]
        inicio_frase = _inicio_de_frase(texto, ini)
        if nomes_lista:
            conf = "alta" if len(partes) >= 2 or not inicio_frase else "media"
            motivo = "Contém prenome/sobrenome frequente"
        elif len(partes) >= 2 and not inicio_frase:
            conf, motivo = "media", "Sequência de palavras com inicial maiúscula"
        elif not inicio_frase and len(nome) >= 3:
            if not incluir_baixa:
                continue
            conf, motivo = "baixa", "Palavra com inicial maiúscula no meio da frase"
        else:
            continue
        cand.append(Candidato(ini, fim, nome, "PESSOA", "heuristica", conf, motivo=motivo))
    # 6. spaCy
    if usar_spacy:
        for c in _spacy(texto):
            if dobrar(c.trecho) not in excecoes_n and dobrar(c.trecho) not in nn and not CODIGO_ANON.search(c.trecho):
                cand.append(c)

    # remove o que cai em termo protegido
    cand = [c for c in cand if not any(_sobrepoe(c.inicio, c.fim, a, b) for a, b in protegidos_pos)]
    # resolve sobreposições: o trecho mais longo vence (ex.: o e-mail inteiro em vez do
    # nome contido nele); em empate, vale a prioridade da fonte.
    mapeados = [c for c in cand if c.fonte == "mapeamento"]
    cand.sort(key=lambda c: (-(c.fim - c.inicio), PRIORIDADE.get(c.fonte, 9), c.inicio))
    escolhidos: list[Candidato] = []
    for c in cand:
        if not any(_sobrepoe(c.inicio, c.fim, e.inicio, e.fim) for e in escolhidos):
            escolhidos.append(c)
    # um trecho maior que contém um valor já mapeado herda o código desse valor
    for c in escolhidos:
        if c.codigo is None and c.categoria in ("PESSOA", "LOCAL"):
            dentro = [m for m in mapeados if c.inicio <= m.inicio and m.fim <= c.fim]
            if dentro:
                c.codigo, c.categoria, c.confianca = dentro[0].codigo, dentro[0].categoria, "alta"
                c.motivo += f"; contém valor já registrado como {dentro[0].codigo}"
    escolhidos.sort(key=lambda c: c.inicio)
    return escolhidos


def protegidos_do_projeto(p) -> list[str]:
    return [t["termo"] for t in p.con.execute("SELECT termo FROM termos WHERE tipo='protegido' AND ativo=1")]


def excecoes_do_projeto(p) -> list[str]:
    return [t["termo"] for t in p.con.execute("SELECT termo FROM termos WHERE tipo='excecao' AND ativo=1")]


def identidades_do_projeto(p) -> list[tuple[str, str, str]]:
    from . import protegido
    out = []
    for i in protegido.identidades(p):
        for v in [i["valor"]] + i["variantes"]:
            if len(v.strip()) >= 2:
                out.append((v, i["codigo"], i["categoria"]))
    return out


def detectar_no_projeto(p, texto: str, incluir_baixa: bool = True) -> list[Candidato]:
    ids = identidades_do_projeto(p) if p.cofre_aberto() else []
    return detectar(texto, protegidos=protegidos_do_projeto(p), excecoes=excecoes_do_projeto(p),
                    identidades=ids, incluir_baixa=incluir_baixa)


def verificar_texto_simples(p, texto: str, somente_novos_em: str | None = None) -> list[dict]:
    """Alertas de possíveis dados pessoais (não bloqueia). Usado ao salvar edições."""
    cands = detectar_no_projeto(p, texto, incluir_baixa=False)
    if somente_novos_em is not None:
        anteriores = {(c.trecho, c.categoria) for c in detectar_no_projeto(p, somente_novos_em, incluir_baixa=False)}
        cands = [c for c in cands if (c.trecho, c.categoria) not in anteriores]
    return [{"trecho": c.trecho, "categoria": c.categoria, "confianca": c.confianca, "motivo": c.motivo,
             "inicio": c.inicio} for c in cands if c.confianca in ("alta", "media")]


def verificar_envio(p, texto: str) -> dict:
    """Verificação de privacidade do conteúdo a ser enviado à IA."""
    cofre = p.cofre_aberto()
    cands = detectar_no_projeto(p, texto, incluir_baixa=False)
    bloqueantes = [c for c in cands if c.fonte == "mapeamento" or (c.fonte == "padrao" and c.confianca == "alta")]
    return {
        "cofre_verificado": cofre,
        "itens": [c.dict() for c in cands],
        "bloqueantes": [c.dict() for c in bloqueantes],
        "aviso": None if cofre else ("O cofre está bloqueado: o conteúdo não foi comparado com o mapeamento de "
                                     "identidades. Desbloqueie o cofre para uma verificação completa."),
    }
