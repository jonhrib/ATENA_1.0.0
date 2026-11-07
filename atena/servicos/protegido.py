"""Operações na zona protegida (cofre): originais, textos identificáveis e mapeamento reversível.

Nada deste módulo é usado na montagem de conteúdo enviado à IA nem nas
exportações comuns. Toda leitura do mapeamento é registrada no histórico do
projeto e no registro de acessos do cofre.
"""
from __future__ import annotations

import json
import re

from ..auditoria import registrar
from ..cofre import cifrar_bytes, cifrar_texto, decifrar_bytes, decifrar_texto, registrar_acesso
from ..db import agora, linha, linhas, transacao
from ..textos import sha256
from .projetos import ErroValidacao, Projeto

CATEGORIAS = {
    "PARTICIPANTE": "Participante",
    "ENTREVISTADOR": "Entrevistador(a)/pesquisador(a)",
    "PESSOA": "Outra pessoa citada",
    "LOCAL": "Local/endereço/cidade",
    "INSTITUICAO": "Instituição/organização (não protegida)",
    "EMAIL": "E-mail",
    "TELEFONE": "Telefone",
    "DOCUMENTO": "CPF/RG/CNPJ/outro documento",
    "ENDERECO": "Endereço/CEP",
    "MATRICULA": "Matrícula/identificador institucional",
    "LINK": "Endereço web/perfil",
    "DATA": "Data identificadora",
    "OUTRO": "Outro dado identificável",
}


# ------------------------------------------------------------------ originais
def salvar_original(p: Projeto, documento_id: int, codigo_doc: str, nome_arquivo: str, dados: bytes) -> str:
    dek = p.dek()
    import uuid
    destino = p.pasta_protegida / f"{codigo_doc}-{uuid.uuid4().hex[:8]}.bin"
    destino.write_bytes(cifrar_bytes(dek, dados, f"original:{documento_id}"))
    h = sha256(dados)
    p.con_cofre.execute(
        "INSERT INTO originais(documento_id, nome_arquivo, arquivo, sha256, tamanho, criado_em) VALUES (?,?,?,?,?,?)",
        (documento_id, cifrar_texto(dek, nome_arquivo, "nome_arquivo"), destino.name, h, len(dados), agora()))
    return h


def ler_original(p: Projeto, documento_id: int) -> tuple[str, bytes]:
    dek = p.dek()
    r = p.con_cofre.execute("SELECT * FROM originais WHERE documento_id = ?", (documento_id,)).fetchone()
    if not r:
        raise ErroValidacao("Este documento não possui arquivo original (foi criado por texto colado).")
    dados = decifrar_bytes(dek, (p.pasta_protegida / r["arquivo"]).read_bytes(), f"original:{documento_id}")
    if sha256(dados) != r["sha256"]:
        raise ErroValidacao("O arquivo original não confere com o hash registrado (possível corrupção).")
    nome = decifrar_texto(dek, r["nome_arquivo"], "nome_arquivo")
    registrar_acesso(p.con_cofre, "leitura_original", f"documento {documento_id}")
    with transacao(p.con):
        registrar(p.con, "cofre", documento_id, "acessar_original",
                  justificativa="Arquivo original decifrado para consulta/baixa.")
    return nome, dados


def info_original(p: Projeto, documento_id: int) -> dict | None:
    r = p.con_cofre.execute("SELECT documento_id, arquivo, sha256, tamanho, criado_em FROM originais"
                            " WHERE documento_id = ?", (documento_id,)).fetchone()
    return linha(r)


def nome_original(p: Projeto, documento_id: int) -> str:
    r = p.con_cofre.execute("SELECT nome_arquivo FROM originais WHERE documento_id = ?", (documento_id,)).fetchone()
    return decifrar_texto(p.dek(), r["nome_arquivo"], "nome_arquivo") if r else ""


def limpar_orfaos(p: Projeto, documento_id: int) -> None:
    """Remove do cofre restos de uma importação que falhou antes de ser confirmada.

    Só é chamada para um ``documento_id`` recém-criado (ainda não confirmado no
    banco do projeto); portanto, qualquer registro protegido com esse
    identificador pertence a uma tentativa anterior que foi desfeita.
    """
    p.con_cofre.execute("DELETE FROM originais WHERE documento_id = ?", (documento_id,))
    p.con_cofre.execute("DELETE FROM textos WHERE documento_id = ?", (documento_id,))


# ------------------------------------------------------------------ textos protegidos
def salvar_texto(p: Projeto, documento_id: int, tipo: str, texto: str, observacao: str = "") -> int:
    dek = p.dek()
    n = (p.con_cofre.execute("SELECT MAX(numero) FROM textos WHERE documento_id = ? AND tipo = ?",
                             (documento_id, tipo)).fetchone()[0] or 0) + 1
    cur = p.con_cofre.execute(
        "INSERT INTO textos(documento_id, tipo, numero, conteudo, sha256, observacao, criado_em) VALUES (?,?,?,?,?,?,?)",
        (documento_id, tipo, n, cifrar_texto(dek, texto, f"texto:{documento_id}:{tipo}:{n}"), sha256(texto),
         observacao, agora()))
    return cur.lastrowid


def versoes_texto(p: Projeto, documento_id: int) -> list[dict]:
    return linhas(p.con_cofre.execute(
        "SELECT id, documento_id, tipo, numero, sha256, observacao, criado_em FROM textos"
        " WHERE documento_id = ? ORDER BY tipo, numero", (documento_id,)))


def ler_texto(p: Projeto, documento_id: int, tipo: str = "revisado", numero: int | None = None) -> dict:
    dek = p.dek()
    if numero is None:
        r = p.con_cofre.execute("SELECT * FROM textos WHERE documento_id = ? AND tipo = ? ORDER BY numero DESC LIMIT 1",
                                (documento_id, tipo)).fetchone()
    else:
        r = p.con_cofre.execute("SELECT * FROM textos WHERE documento_id = ? AND tipo = ? AND numero = ?",
                                (documento_id, tipo, numero)).fetchone()
    if not r:
        raise ErroValidacao("Texto protegido não encontrado.")
    d = dict(r)
    d["texto"] = decifrar_texto(dek, r["conteudo"], f"texto:{documento_id}:{r['tipo']}:{r['numero']}")
    del d["conteudo"]
    return d


def ler_texto_id(p: Projeto, texto_id: int) -> dict:
    r = p.con_cofre.execute("SELECT * FROM textos WHERE id = ?", (texto_id,)).fetchone()
    if not r:
        raise ErroValidacao("Texto protegido não encontrado.")
    return ler_texto(p, r["documento_id"], r["tipo"], r["numero"])


# ------------------------------------------------------------------ identidades
def _proximo_codigo(p: Projeto, categoria: str) -> str:
    r = p.con_cofre.execute("SELECT ultimo FROM contadores WHERE categoria = ?", (categoria,)).fetchone()
    n = (r[0] if r else 0) + 1
    # nunca reutiliza um código já existente
    while p.con_cofre.execute("SELECT 1 FROM identidades WHERE codigo = ?", (f"{categoria}_{n:03d}",)).fetchone():
        n += 1
    p.con_cofre.execute("INSERT INTO contadores(categoria, ultimo) VALUES (?, ?) "
                        "ON CONFLICT(categoria) DO UPDATE SET ultimo = excluded.ultimo", (categoria, n))
    return f"{categoria}_{n:03d}"


def _decifrar_identidade(dek, r) -> dict:
    d = dict(r)
    d["valor"] = decifrar_texto(dek, r["valor"], f"identidade:{r['codigo']}")
    d["variantes"] = json.loads(decifrar_texto(dek, r["variantes"], f"variantes:{r['codigo']}") or "[]")
    d["observacao"] = decifrar_texto(dek, r["observacao"], f"obs:{r['codigo']}") if r["observacao"] else ""
    return d


def identidades(p: Projeto, *, registrar_consulta: bool = False, motivo: str = "") -> list[dict]:
    dek = p.dek()
    out = [_decifrar_identidade(dek, r) for r in
           p.con_cofre.execute("SELECT * FROM identidades WHERE ativo = 1 ORDER BY categoria, codigo")]
    if registrar_consulta:
        registrar_acesso(p.con_cofre, "consulta_mapeamento", motivo)
        with transacao(p.con):
            registrar(p.con, "cofre", None, "consultar_mapeamento", justificativa=motivo or "consulta autorizada",
                      depois={"identidades_exibidas": len(out)})
    return out


def identidade_por_codigo(p: Projeto, codigo: str) -> dict | None:
    r = p.con_cofre.execute("SELECT * FROM identidades WHERE codigo = ?", (codigo,)).fetchone()
    return _decifrar_identidade(p.dek(), r) if r else None


def criar_identidade(p: Projeto, categoria: str, valor: str, variantes: list[str] | None = None, *,
                     codigo: str | None = None, participante_id: int | None = None, observacao: str = "") -> dict:
    categoria = categoria.upper()
    if categoria not in CATEGORIAS:
        raise ErroValidacao("Categoria de identificação inválida.")
    valor = " ".join((valor or "").split())
    if not valor:
        raise ErroValidacao("Informe o valor original a ser protegido.")
    dek = p.dek()
    if codigo is None:
        codigo = "ENTREVISTADOR" if categoria == "ENTREVISTADOR" and not p.con_cofre.execute(
            "SELECT 1 FROM identidades WHERE codigo='ENTREVISTADOR'").fetchone() else _proximo_codigo(p, categoria)
    if not re.fullmatch(r"[A-Z][A-Z_]*(_\d{3,})?", codigo):
        raise ErroValidacao("Código de substituição inválido: use letras maiúsculas, '_' e números (ex.: PESSOA_004).")
    if p.con_cofre.execute("SELECT 1 FROM identidades WHERE codigo = ?", (codigo,)).fetchone():
        raise ErroValidacao(f"O código {codigo} já está em uso.")
    variantes = sorted({" ".join(v.split()) for v in (variantes or []) if v.strip() and v.strip() != valor})
    momento = agora()
    p.con_cofre.execute(
        "INSERT INTO identidades(codigo, categoria, valor, variantes, participante_id, observacao, criado_em, atualizado_em)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (codigo, categoria, cifrar_texto(dek, valor, f"identidade:{codigo}"),
         cifrar_texto(dek, json.dumps(variantes, ensure_ascii=False), f"variantes:{codigo}"), participante_id,
         cifrar_texto(dek, observacao, f"obs:{codigo}") if observacao else None, momento, momento))
    with transacao(p.con):
        registrar(p.con, "cofre", codigo, "criar_identidade",
                  depois={"codigo": codigo, "categoria": categoria, "n_variantes": len(variantes)},
                  justificativa="Valor original guardado cifrado no cofre.")
    return identidade_por_codigo(p, codigo)


def atualizar_identidade(p: Projeto, codigo: str, *, valor: str | None = None, variantes: list[str] | None = None,
                         observacao: str | None = None) -> None:
    atual = identidade_por_codigo(p, codigo)
    if not atual:
        raise ErroValidacao("Identidade não encontrada.")
    dek = p.dek()
    novo_valor = " ".join((valor if valor is not None else atual["valor"]).split())
    novas_var = sorted({" ".join(v.split()) for v in (variantes if variantes is not None else atual["variantes"])
                        if v.strip() and v.strip() != novo_valor})
    obs = observacao if observacao is not None else atual["observacao"]
    p.con_cofre.execute(
        "UPDATE identidades SET valor = ?, variantes = ?, observacao = ?, atualizado_em = ? WHERE codigo = ?",
        (cifrar_texto(dek, novo_valor, f"identidade:{codigo}"),
         cifrar_texto(dek, json.dumps(novas_var, ensure_ascii=False), f"variantes:{codigo}"),
         cifrar_texto(dek, obs, f"obs:{codigo}") if obs else None, agora(), codigo))
    with transacao(p.con):
        registrar(p.con, "cofre", codigo, "editar_identidade",
                  depois={"codigo": codigo, "n_variantes": len(novas_var), "valor_alterado": novo_valor != atual["valor"]})


def adicionar_variante(p: Projeto, codigo: str, variante: str) -> None:
    atual = identidade_por_codigo(p, codigo)
    if atual and variante.strip() and variante.strip() != atual["valor"] and variante.strip() not in atual["variantes"]:
        atualizar_identidade(p, codigo, variantes=atual["variantes"] + [variante.strip()])


def identidade_participante(p: Projeto, participante_id: int, codigo: str) -> dict | None:
    r = p.con_cofre.execute("SELECT * FROM identidades WHERE codigo = ?", (codigo,)).fetchone()
    return _decifrar_identidade(p.dek(), r) if r else None


def termos_identificaveis(p: Projeto) -> list[tuple[str, str]]:
    """(valor, código) de todas as identidades e variantes — uso interno da verificação de privacidade."""
    out = []
    for i in identidades(p):
        for v in [i["valor"]] + i["variantes"]:
            if len(v) >= 3:
                out.append((v, i["codigo"]))
    return out
