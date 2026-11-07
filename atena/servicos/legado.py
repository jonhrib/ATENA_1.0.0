"""Importação do sistema anterior do TCC (pasta ``Desenvolvimento``, linha de comando).

Aproveita:
* ``transcricoes/processadas/PARTICIPANTE_XXX.txt`` → documentos já anonimizados,
  ligados a participantes com o MESMO código;
* ``dados/participantes.csv`` → metadados não identificáveis dos participantes;
* ``dados/entidades_preservar.txt`` → termos protegidos (IES, Empresas Juniores…);
* ``dados/falsos_positivos.txt`` → exceções da detecção;
* opcionalmente, ``transcricoes/mapeamentos/mapeamentos.enc`` + a chave Fernet
  (``chave.key``) → mapeamento reversível, recifrado no cofre do ATENA.

As transcrições brutas e revisadas (que contêm nomes reais) não são importadas.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

from ..auditoria import registrar
from ..db import agora, transacao
from . import documentos, participantes, projetos, protegido
from .projetos import ErroValidacao, Projeto

CAMPOS_CSV = {"universidade": "Instituição", "curso": "Curso", "municipio": "Município", "empresa_junior": "Empresa Júnior",
              "vinculo_ej": "Vínculo com a EJ", "situacao_academica": "Situação acadêmica", "etapa_curso": "Período/etapa",
              "perfil": "Grupo/perfil"}


class _Fonte:
    """Lê arquivos de uma pasta ou de um ZIP com a mesma interface."""

    def __init__(self, caminho: Path | None = None, zip_bytes: bytes | None = None):
        self.zip = zipfile.ZipFile(io.BytesIO(zip_bytes)) if zip_bytes else None
        self.pasta = Path(caminho) if caminho else None
        nomes = self.zip.namelist() if self.zip else [str(x.relative_to(self.pasta)).replace("\\", "/")
                                                      for x in self.pasta.rglob("*") if x.is_file()]
        # localiza a raiz (pasta que contém transcricoes/processadas)
        raiz = None
        for n in nomes:
            m = re.match(r"^(.*?)transcricoes/processadas/[^/]+\.txt$", n)
            if m:
                raiz = m[1]
                break
        if raiz is None:
            raise ErroValidacao("Não encontrei a pasta 'transcricoes/processadas' do sistema anterior.")
        self.raiz = raiz
        self.nomes = nomes

    def ler(self, rel: str) -> bytes | None:
        n = self.raiz + rel
        if self.zip:
            return self.zip.read(n) if n in self.nomes else None
        f = self.pasta / n
        return f.read_bytes() if f.is_file() else None

    def listar(self, prefixo: str) -> list[str]:
        pre = self.raiz + prefixo
        return sorted(n[len(self.raiz):] for n in self.nomes if n.startswith(pre))


def _lista(b: bytes | None) -> list[str]:
    if not b:
        return []
    out = []
    for l in b.decode("utf-8", errors="replace").splitlines():
        l = l.split("#", 1)[0].strip()
        if l:
            out.append(l)
    return out


def analisar(caminho: Path | None = None, zip_bytes: bytes | None = None) -> dict:
    f = _Fonte(caminho, zip_bytes)
    proc = [n for n in f.listar("transcricoes/processadas/") if re.search(r"PARTICIPANTE_\d{3}\.txt$", n)]
    return {"transcricoes": [Path(n).name for n in proc],
            "tem_participantes_csv": f.ler("dados/participantes.csv") is not None,
            "termos_protegidos": len(_lista(f.ler("dados/entidades_preservar.txt"))),
            "excecoes": len(_lista(f.ler("dados/falsos_positivos.txt"))),
            "tem_mapeamento": f.ler("transcricoes/mapeamentos/mapeamentos.enc") is not None}


def importar(p: Projeto, *, caminho: Path | None = None, zip_bytes: bytes | None = None, chave_fernet: bytes | None = None,
             importar_excecoes: bool = False) -> dict:
    p.dek()
    f = _Fonte(caminho, zip_bytes)
    rel = {"participantes": 0, "documentos": 0, "termos": 0, "excecoes": 0, "identidades": 0, "avisos": []}
    termos = _lista(f.ler("dados/entidades_preservar.txt"))
    rel["termos"] = projetos.adicionar_termos(p, "protegido", termos, "importado do sistema anterior")
    if importar_excecoes:
        rel["excecoes"] = projetos.adicionar_termos(p, "excecao", _lista(f.ler("dados/falsos_positivos.txt")),
                                                    "importado do sistema anterior")
    meta = {}
    b = f.ler("dados/participantes.csv")
    if b:
        for row in csv.DictReader(io.StringIO(b.decode("utf-8-sig", errors="replace"))):
            cod = (row.get("codigo") or "").strip()
            if cod:
                meta[cod] = {CAMPOS_CSV.get(k, k): (v or "").strip() for k, v in row.items() if k not in ("codigo", "observacoes") and (v or "").strip()}
                meta[cod]["_obs"] = (row.get("observacoes") or "").strip()
    if meta:
        campos = list(dict.fromkeys(json.loads(p.dados()["campos_participante"] or "[]") + list(CAMPOS_CSV.values())))
        projetos.definir_campos_participante(p, campos)
    for n in [x for x in f.listar("transcricoes/processadas/") if re.search(r"PARTICIPANTE_\d{3}\.txt$", x)]:
        cod = re.search(r"(PARTICIPANTE_\d{3})", n)[1]
        num = int(cod[-3:])
        part = participantes.por_codigo(p, cod)
        if not part:
            if p.con.execute("SELECT 1 FROM participantes WHERE numero = ?", (num,)).fetchone():
                rel["avisos"].append(f"Número {num} já usado por outro participante; {n} foi ignorado.")
                continue
            m = dict(meta.get(cod, {}))
            obs = m.pop("_obs", "")
            with transacao(p.con):
                cur = p.con.execute("INSERT INTO participantes(numero, codigo, metadados, situacao, observacoes, criado_em, atualizado_em)"
                                    " VALUES (?,?,?,?,?,?,?)",
                                    (num, cod, json.dumps(m, ensure_ascii=False), "anonimizada", obs, agora(), agora()))
                registrar(p.con, "participante", cur.lastrowid, "criar", depois={"codigo": cod, "metadados": m},
                          justificativa="importado do sistema anterior")
            part = participantes.por_codigo(p, cod)
            rel["participantes"] += 1
        texto = f.ler(n).decode("utf-8", errors="replace")
        documentos.importar(p, Path(n).name, texto.encode("utf-8"), titulo=f"Entrevista {cod}", tipo="entrevista",
                            participante_id=part["id"], ja_anonimizado=True,
                            observacoes="Importada do sistema anterior (transcricoes/processadas).")
        rel["documentos"] += 1
    enc = f.ler("transcricoes/mapeamentos/mapeamentos.enc")
    if enc and chave_fernet:
        from cryptography.fernet import Fernet, InvalidToken
        try:
            mapa = json.loads(Fernet(chave_fernet.strip()).decrypt(enc).decode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise ErroValidacao("A chave informada não abre o mapeamento do sistema anterior.") from exc
        rel["identidades"] = _importar_mapeamento(p, mapa, rel)
    elif enc:
        rel["avisos"].append("O mapeamento cifrado foi encontrado, mas nenhuma chave foi informada: as identidades não foram importadas.")
    return rel


def _importar_mapeamento(p: Projeto, mapa: dict, rel: dict) -> int:
    n = 0
    vistos = {}

    def guardar(codigo, categoria, valor):
        nonlocal n
        if not codigo or not valor or not re.fullmatch(r"[A-Z][A-Z_]*(_\d{3,})?", codigo):
            return
        if codigo in vistos:
            if valor not in vistos[codigo]:
                protegido.adicionar_variante(p, codigo, valor)
                vistos[codigo].append(valor)
            return
        if protegido.identidade_por_codigo(p, codigo):
            protegido.adicionar_variante(p, codigo, valor)
        else:
            protegido.criar_identidade(p, categoria, valor, [], codigo=codigo)
            n += 1
        vistos[codigo] = [valor]
        m = re.search(r"_(\d{3,})$", codigo)
        if m:
            cat = codigo[:m.start()]
            p.con_cofre.execute("INSERT INTO contadores(categoria, ultimo) VALUES (?, ?) ON CONFLICT(categoria) DO UPDATE"
                                " SET ultimo = MAX(ultimo, excluded.ultimo)", (cat, int(m[1])))

    for _arq, e in (mapa.get("entrevistas") or {}).items():
        guardar(e.get("codigo"), "PARTICIPANTE", e.get("participante"))
        for nome, cod in (e.get("pessoas") or {}).items():
            guardar(cod, "LOCAL" if str(cod).startswith("LOCAL") else "PESSOA", nome)
        if e.get("entrevistador") and e.get("entrevistador") != "ENTREVISTADOR":
            guardar("ENTREVISTADOR", "ENTREVISTADOR", e["entrevistador"])
    for nome, cod in (mapa.get("pessoas_globais") or {}).items():
        if isinstance(cod, str):
            guardar(cod, "LOCAL" if cod.startswith("LOCAL") else "PESSOA", nome)
    return n
