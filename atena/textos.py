"""Utilidades de texto: normalização, turnos de fala, comparação e realocação de posições."""
from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass


def normalizar(texto: str) -> str:
    """Forma canônica para armazenamento: NFC, quebras de linha \\n, sem BOM."""
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    texto = texto.replace("\f", "\n").replace(" ", " ")
    return unicodedata.normalize("NFC", texto)


def sha256(texto: str | bytes) -> str:
    if isinstance(texto, str):
        texto = texto.encode("utf-8")
    return hashlib.sha256(texto).hexdigest()


def dobrar(texto: str) -> str:
    """Minúsculas sem acento, preservando o comprimento (1 caractere → 1 caractere)."""
    out = []
    for c in texto:
        d = unicodedata.normalize("NFD", c)
        out.append((d[0] if d else c).lower()[:1] or c)
    return "".join(out)


def contexto(texto: str, ini: int, fim: int, largura: int = 80) -> dict:
    a, b = max(0, ini - largura), min(len(texto), fim + largura)
    return {"antes": ("…" if a else "") + texto[a:ini], "trecho": texto[ini:fim],
            "depois": texto[fim:b] + ("…" if b < len(texto) else "")}


# ------------------------------------------------------------------ turnos de fala
ROTULO = re.compile(
    r"^(?P<locutor>[^\n]{1,80}?)\s+-\s+"
    r"(?P<horario>\d{1,2}:\s*\d{2}(?::\s*\d{2})?(?:\s*[AaPp][Mm])?)"
    r":?\s*$"
)


@dataclass
class Turno:
    locutor: str | None
    horario: str | None
    inicio: int  # posição do início do rótulo
    inicio_texto: int
    fim: int


def turnos(texto: str) -> list[Turno]:
    """Divide uma transcrição no formato ``Locutor - hh:mm:`` em turnos com posições."""
    out: list[Turno] = []
    pos = 0
    atual: Turno | None = None
    for ln in texto.split("\n"):
        m = ROTULO.match(ln.strip())
        if m:
            if atual:
                atual.fim = pos
                out.append(atual)
            atual = Turno(m["locutor"].strip(), m["horario"].strip(), pos, pos + len(ln) + 1, pos)
        elif atual is None and ln.strip():
            atual = Turno(None, None, pos, pos, pos)
        pos += len(ln) + 1
    if atual:
        atual.fim = len(texto)
        out.append(atual)
    return out


def rotulos_locutor(texto: str) -> list[tuple[int, int, str]]:
    """Posições (início, fim, nome) dos nomes de locutor nos rótulos."""
    out = []
    pos = 0
    for ln in texto.split("\n"):
        m = ROTULO.match(ln)
        if m:
            out.append((pos + m.start("locutor"), pos + m.end("locutor"), m["locutor"]))
        pos += len(ln) + 1
    return out


# ------------------------------------------------------------------ comparação de versões
def _blocos(antigo: str, novo: str):
    """Opcodes por linha, refinados por caractere dentro dos blocos alterados."""
    la, lb = antigo.splitlines(keepends=True), novo.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    pa = [0]
    for x in la:
        pa.append(pa[-1] + len(x))
    pb = [0]
    for x in lb:
        pb.append(pb[-1] + len(x))
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a1, a2, b1, b2 = pa[i1], pa[i2], pb[j1], pb[j2]
        if tag == "equal":
            yield ("equal", a1, a2, b1, b2)
        elif tag == "replace" and (a2 - a1) < 20000 and (b2 - b1) < 20000:
            sub = difflib.SequenceMatcher(None, antigo[a1:a2], novo[b1:b2], autojunk=False)
            for t2, x1, x2, y1, y2 in sub.get_opcodes():
                yield (t2, a1 + x1, a1 + x2, b1 + y1, b1 + y2)
        else:
            yield (tag, a1, a2, b1, b2)


class MapaPosicoes:
    """Converte posições do texto antigo em posições do texto novo."""

    def __init__(self, antigo: str, novo: str):
        self.blocos = list(_blocos(antigo, novo))
        self.inicios = [b[1] for b in self.blocos]

    def _bloco(self, pos: int):
        i = max(0, bisect_right(self.inicios, pos) - 1)
        return self.blocos[i] if self.blocos else ("equal", 0, 0, 0, 0)

    def converter(self, pos: int) -> int:
        tag, a1, a2, b1, b2 = self._bloco(pos)
        if tag == "equal":
            return b1 + (pos - a1)
        return b1 if pos <= a1 else b2

    def intervalo_intacto(self, ini: int, fim: int) -> bool:
        """True se nenhum caractere de [ini, fim) foi alterado."""
        for tag, a1, a2, b1, b2 in self.blocos:
            if tag == "equal":
                continue
            if tag == "insert":
                # inserção exatamente na borda não altera o trecho
                if ini < a1 < fim:
                    return False
            elif a1 < fim and a2 > ini:
                return False
        return True


def realocar(antigo: str, novo: str, ini: int, fim: int, trecho: str, mapa: MapaPosicoes | None = None):
    """Localiza um trecho do texto antigo no texto novo.

    Retorna (situacao, novo_ini, novo_fim) onde situação é:
      'ok'           — o trecho não foi tocado (posições apenas convertidas);
      'realocado'    — o texto do trecho foi reencontrado intacto em outra posição;
      'inconsistente'— o trecho foi alterado ou removido; precisa de revisão.
    """
    mapa = mapa or MapaPosicoes(antigo, novo)
    n_ini, n_fim = mapa.converter(ini), mapa.converter(fim)
    if mapa.intervalo_intacto(ini, fim) and novo[n_ini:n_fim] == trecho:
        return ("ok" if (n_ini, n_fim) == (ini, fim) else "ok_deslocado", n_ini, n_fim)
    # procura o mesmo texto; escolhe a ocorrência mais próxima da posição prevista
    ocorr = [m.start() for m in re.finditer(re.escape(trecho), novo)] if trecho else []
    if ocorr:
        melhor = min(ocorr, key=lambda x: abs(x - n_ini))
        return ("realocado", melhor, melhor + len(trecho))
    if n_fim <= n_ini:
        n_fim = min(len(novo), n_ini + max(1, len(trecho)))
        n_ini = max(0, min(n_ini, n_fim - 1))
    return ("inconsistente", n_ini, n_fim)


def diff_linhas(antigo: str, novo: str) -> list[dict]:
    """Diferenças linha a linha para exibição (tipo: igual, removida, inserida)."""
    out = []
    la, lb = antigo.splitlines(), novo.splitlines()
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            bloco = la[i1:i2]
            if len(bloco) > 6:
                out += [{"tipo": "igual", "a": i1 + k + 1, "b": j1 + k + 1, "texto": bloco[k]} for k in range(3)]
                out.append({"tipo": "omitido", "texto": f"… {len(bloco) - 6} linhas iguais …"})
                out += [{"tipo": "igual", "a": i2 - 3 + k + 1, "b": j2 - 3 + k + 1, "texto": bloco[-3 + k]} for k in range(3)]
            else:
                out += [{"tipo": "igual", "a": i1 + k + 1, "b": j1 + k + 1, "texto": t} for k, t in enumerate(bloco)]
        else:
            out += [{"tipo": "removida", "a": i1 + k + 1, "texto": t} for k, t in enumerate(la[i1:i2])]
            out += [{"tipo": "inserida", "b": j1 + k + 1, "texto": t} for k, t in enumerate(lb[j1:j2])]
    return out


def estatisticas_diff(antigo: str, novo: str) -> dict:
    d = diff_linhas(antigo, novo)
    return {"linhas_removidas": sum(1 for x in d if x["tipo"] == "removida"),
            "linhas_inseridas": sum(1 for x in d if x["tipo"] == "inserida"),
            "caracteres_antes": len(antigo), "caracteres_depois": len(novo)}
