"""Bancos SQLite do ATENA.

Cada projeto tem dois bancos independentes:

* ``projeto.sqlite`` — zona de trabalho. Guarda apenas texto anonimizado,
  a análise (códigos, temas, notas, relatórios) e o histórico append-only.
* ``cofre.sqlite`` — zona protegida. Guarda o mapeamento reversível, os
  textos ainda não anonimizados e as decisões de anonimização. Todo
  conteúdo identificável é cifrado (AES-256-GCM) antes de ser gravado.

As conexões usam WAL, ``synchronous=FULL`` e chaves estrangeiras ativas.
Toda escrita ocorre dentro de ``transacao()`` (BEGIN IMMEDIATE), de modo que
a alteração e o seu registro no histórico são gravados juntos ou não são
gravados.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import VERSAO_ESQUEMA


def agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


ESQUEMA_PROJETO = """
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS projeto(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    uid TEXT NOT NULL,
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    objetivo TEXT DEFAULT '',
    problema TEXT DEFAULT '',
    pergunta TEXT DEFAULT '',
    metodologia TEXT DEFAULT 'Análise temática indutiva (Braun e Clarke, 2006)',
    referencias TEXT DEFAULT '',
    responsavel TEXT DEFAULT '',
    situacao TEXT NOT NULL DEFAULT 'ativo' CHECK (situacao IN ('ativo','arquivado')),
    campos_participante TEXT DEFAULT '[]',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS termos(
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('protegido','excecao')),
    termo TEXT NOT NULL,
    observacao TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    UNIQUE(tipo, termo)
);

CREATE TABLE IF NOT EXISTS participantes(
    id INTEGER PRIMARY KEY,
    numero INTEGER NOT NULL UNIQUE,
    codigo TEXT NOT NULL UNIQUE,
    metadados TEXT NOT NULL DEFAULT '{}',
    situacao TEXT NOT NULL DEFAULT 'prevista',
    observacoes TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos(
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    titulo TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'entrevista',
    participante_id INTEGER REFERENCES participantes(id),
    extensao TEXT DEFAULT '',
    tamanho INTEGER DEFAULT 0,
    sha256_original TEXT DEFAULT '',
    situacao TEXT NOT NULL DEFAULT 'importado',
    ja_anonimizado INTEGER NOT NULL DEFAULT 0,
    data_coleta TEXT DEFAULT '',
    observacoes TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcricao_versoes(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL REFERENCES documentos(id),
    numero INTEGER NOT NULL,
    texto TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    origem TEXT NOT NULL,
    observacao TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE(documento_id, numero)
);

CREATE TABLE IF NOT EXISTS fragmentos(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL REFERENCES documentos(id),
    versao_id INTEGER NOT NULL REFERENCES transcricao_versoes(id),
    inicio INTEGER NOT NULL,
    fim INTEGER NOT NULL,
    texto TEXT NOT NULL,
    prefixo TEXT DEFAULT '',
    sufixo TEXT DEFAULT '',
    situacao TEXT NOT NULL DEFAULT 'ok' CHECK (situacao IN ('ok','realocado','inconsistente')),
    relevante INTEGER NOT NULL DEFAULT 0,
    nota TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    CHECK (fim > inicio)
);

CREATE TABLE IF NOT EXISTS codigos(
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    justificativa TEXT DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual','ia')),
    situacao TEXT NOT NULL DEFAULT 'ativo' CHECK (situacao IN ('ativo','excluido','mesclado')),
    cor TEXT DEFAULT '',
    mesclado_em INTEGER REFERENCES codigos(id),
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codificacoes(
    id INTEGER PRIMARY KEY,
    fragmento_id INTEGER NOT NULL REFERENCES fragmentos(id),
    codigo_id INTEGER NOT NULL REFERENCES codigos(id),
    justificativa TEXT DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'manual',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    removido_em TEXT
);

CREATE TABLE IF NOT EXISTS temas(
    id INTEGER PRIMARY KEY,
    pai_id INTEGER REFERENCES temas(id),
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    conceito_central TEXT DEFAULT '',
    escopo TEXT DEFAULT '',
    limites TEXT DEFAULT '',
    relacao_pergunta TEXT DEFAULT '',
    justificativa TEXT DEFAULT '',
    situacao TEXT NOT NULL DEFAULT 'candidato'
        CHECK (situacao IN ('candidato','em_revisao','revisado','definido','descartado','fundido')),
    definicao_confirmada_em TEXT,
    definicao_desatualizada INTEGER NOT NULL DEFAULT 0,
    origem TEXT NOT NULL DEFAULT 'manual',
    fundido_em INTEGER REFERENCES temas(id),
    cor TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tema_codigos(
    id INTEGER PRIMARY KEY,
    tema_id INTEGER NOT NULL REFERENCES temas(id),
    codigo_id INTEGER NOT NULL REFERENCES codigos(id),
    justificativa TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    removido_em TEXT
);

CREATE TABLE IF NOT EXISTS tema_evidencias(
    id INTEGER PRIMARY KEY,
    tema_id INTEGER NOT NULL REFERENCES temas(id),
    fragmento_id INTEGER NOT NULL REFERENCES fragmentos(id),
    comentario TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisoes_tema(
    id INTEGER PRIMARY KEY,
    tema_id INTEGER NOT NULL REFERENCES temas(id),
    nivel INTEGER NOT NULL CHECK (nivel IN (1,2)),
    coerencia TEXT DEFAULT '',
    distincao TEXT DEFAULT '',
    sobreposicao TEXT DEFAULT '',
    decisao TEXT DEFAULT '',
    observacao TEXT DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notas(
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL DEFAULT 'observacao',
    fase INTEGER,
    titulo TEXT DEFAULT '',
    texto TEXT NOT NULL,
    documento_id INTEGER REFERENCES documentos(id),
    fragmento_id INTEGER REFERENCES fragmentos(id),
    entidade TEXT DEFAULT '',
    entidade_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leituras(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL REFERENCES documentos(id),
    versao_id INTEGER REFERENCES transcricao_versoes(id),
    tipo TEXT NOT NULL DEFAULT 'abertura',
    observacao TEXT DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisoes(
    id INTEGER PRIMARY KEY,
    fase INTEGER,
    titulo TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    justificativa TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fases(
    numero INTEGER PRIMARY KEY CHECK (numero BETWEEN 1 AND 6),
    situacao TEXT NOT NULL DEFAULT 'nao_iniciada'
        CHECK (situacao IN ('nao_iniciada','em_andamento','concluida')),
    iniciada_em TEXT,
    concluida_em TEXT,
    observacao TEXT DEFAULT '',
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS relatorios(
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    situacao TEXT NOT NULL DEFAULT 'rascunho' CHECK (situacao IN ('rascunho','aprovado')),
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relatorio_versoes(
    id INTEGER PRIMARY KEY,
    relatorio_id INTEGER NOT NULL REFERENCES relatorios(id),
    numero INTEGER NOT NULL,
    conteudo TEXT NOT NULL,
    origem TEXT NOT NULL DEFAULT 'pesquisador',
    observacao TEXT DEFAULT '',
    aprovado INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    UNIQUE(relatorio_id, numero)
);

CREATE TABLE IF NOT EXISTS ia_interacoes(
    id INTEGER PRIMARY KEY,
    operacao TEXT NOT NULL,
    finalidade TEXT NOT NULL,
    modelo TEXT DEFAULT '',
    registros TEXT DEFAULT '[]',
    conteudo_enviado TEXT NOT NULL,
    verificacao_privacidade TEXT DEFAULT '{}',
    situacao TEXT NOT NULL DEFAULT 'preparada'
        CHECK (situacao IN ('preparada','enviada','respondida','erro','cancelada')),
    confirmado_em TEXT,
    resposta TEXT,
    erro TEXT,
    tokens_entrada INTEGER DEFAULT 0,
    tokens_saida INTEGER DEFAULT 0,
    custo_estimado REAL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sugestoes(
    id INTEGER PRIMARY KEY,
    interacao_id INTEGER REFERENCES ia_interacoes(id),
    tipo TEXT NOT NULL,
    conteudo TEXT NOT NULL,
    alertas TEXT DEFAULT '[]',
    situacao TEXT NOT NULL DEFAULT 'pendente'
        CHECK (situacao IN ('pendente','aceita','editada','rejeitada')),
    decisao_observacao TEXT DEFAULT '',
    resultado TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    decidido_em TEXT
);

CREATE TABLE IF NOT EXISTS exportacoes(
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    formato TEXT NOT NULL,
    arquivo TEXT NOT NULL,
    protegida INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historico(
    id INTEGER PRIMARY KEY,
    momento TEXT NOT NULL,
    entidade TEXT NOT NULL,
    entidade_id TEXT,
    operacao TEXT NOT NULL,
    fase INTEGER,
    antes TEXT,
    depois TEXT,
    justificativa TEXT DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'pesquisador' CHECK (origem IN ('pesquisador','ia','sistema')),
    hash_anterior TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_hist_entidade ON historico(entidade, entidade_id);
CREATE INDEX IF NOT EXISTS ix_frag_doc ON fragmentos(documento_id, ativo);
CREATE INDEX IF NOT EXISTS ix_codif_frag ON codificacoes(fragmento_id, ativo);
CREATE INDEX IF NOT EXISTS ix_codif_cod ON codificacoes(codigo_id, ativo);
CREATE INDEX IF NOT EXISTS ix_tc_tema ON tema_codigos(tema_id, ativo);
CREATE INDEX IF NOT EXISTS ix_versoes_doc ON transcricao_versoes(documento_id, numero);

-- Histórico append-only: nenhuma linha pode ser alterada ou apagada.
CREATE TRIGGER IF NOT EXISTS historico_sem_update BEFORE UPDATE ON historico
BEGIN SELECT RAISE(ABORT, 'O histórico do ATENA não pode ser alterado.'); END;
CREATE TRIGGER IF NOT EXISTS historico_sem_delete BEFORE DELETE ON historico
BEGIN SELECT RAISE(ABORT, 'O histórico do ATENA não pode ser apagado.'); END;

-- Versões de transcrição e de relatório também são imutáveis.
CREATE TRIGGER IF NOT EXISTS versoes_sem_update BEFORE UPDATE ON transcricao_versoes
BEGIN SELECT RAISE(ABORT, 'Versões de transcrição são imutáveis.'); END;
CREATE TRIGGER IF NOT EXISTS versoes_sem_delete BEFORE DELETE ON transcricao_versoes
BEGIN SELECT RAISE(ABORT, 'Versões de transcrição são imutáveis.'); END;
CREATE TRIGGER IF NOT EXISTS relver_sem_delete BEFORE DELETE ON relatorio_versoes
BEGIN SELECT RAISE(ABORT, 'Versões de relatório são imutáveis.'); END;
CREATE TRIGGER IF NOT EXISTS relver_sem_update_conteudo BEFORE UPDATE OF conteudo ON relatorio_versoes
BEGIN SELECT RAISE(ABORT, 'Versões de relatório são imutáveis.'); END;
CREATE TRIGGER IF NOT EXISTS ia_sem_delete BEFORE DELETE ON ia_interacoes
BEGIN SELECT RAISE(ABORT, 'O registro de envios à IA não pode ser apagado.'); END;
"""

ESQUEMA_COFRE = """
CREATE TABLE IF NOT EXISTS cofre_meta(chave TEXT PRIMARY KEY, valor TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS originais(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL UNIQUE,
    nome_arquivo BLOB NOT NULL,
    arquivo TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    tamanho INTEGER NOT NULL,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS textos(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('extraido','revisado')),
    numero INTEGER NOT NULL,
    conteudo BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    observacao TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE(documento_id, tipo, numero)
);

CREATE TABLE IF NOT EXISTS identidades(
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    categoria TEXT NOT NULL,
    valor BLOB NOT NULL,
    variantes BLOB NOT NULL,
    participante_id INTEGER,
    observacao BLOB,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contadores(categoria TEXT PRIMARY KEY, ultimo INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS sessoes(
    id INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL,
    texto_id INTEGER NOT NULL REFERENCES textos(id),
    situacao TEXT NOT NULL DEFAULT 'em_revisao' CHECK (situacao IN ('em_revisao','aplicada','cancelada')),
    versao_gerada INTEGER,
    criado_em TEXT NOT NULL,
    aplicada_em TEXT
);

CREATE TABLE IF NOT EXISTS itens(
    id INTEGER PRIMARY KEY,
    sessao_id INTEGER NOT NULL REFERENCES sessoes(id),
    inicio INTEGER NOT NULL,
    fim INTEGER NOT NULL,
    trecho BLOB NOT NULL,
    categoria TEXT NOT NULL,
    fonte TEXT NOT NULL,
    confianca TEXT NOT NULL DEFAULT 'media',
    decisao TEXT NOT NULL DEFAULT 'pendente'
        CHECK (decisao IN ('pendente','anonimizar','manter','rejeitar')),
    codigo TEXT,
    identidade_id INTEGER REFERENCES identidades(id),
    observacao TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    decidido_em TEXT
);

CREATE TABLE IF NOT EXISTS acessos(
    id INTEGER PRIMARY KEY,
    momento TEXT NOT NULL,
    operacao TEXT NOT NULL,
    detalhe TEXT DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS acessos_sem_delete BEFORE DELETE ON acessos
BEGIN SELECT RAISE(ABORT, 'O registro de acessos ao cofre não pode ser apagado.'); END;
CREATE TRIGGER IF NOT EXISTS textos_sem_update BEFORE UPDATE ON textos
BEGIN SELECT RAISE(ABORT, 'Versões protegidas são imutáveis.'); END;
"""


def conectar(caminho: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(caminho), timeout=30, isolation_level=None,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


@contextmanager
def transacao(con: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT; desfaz tudo em caso de erro.

    Transações aninhadas viram SAVEPOINTs, de modo que um serviço pode chamar
    outro sem quebrar a atomicidade.
    """
    if con.in_transaction:
        nome = f"sp_{id(object())}"
        con.execute(f"SAVEPOINT {nome}")
        try:
            yield con
        except BaseException:
            con.execute(f"ROLLBACK TO {nome}")
            con.execute(f"RELEASE {nome}")
            raise
        else:
            con.execute(f"RELEASE {nome}")
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def inicializar_projeto(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA_PROJETO)
    con.execute("INSERT OR IGNORE INTO meta(chave, valor) VALUES ('versao_esquema', ?)",
                (str(VERSAO_ESQUEMA),))
    for n in range(1, 7):
        con.execute("INSERT OR IGNORE INTO fases(numero) VALUES (?)", (n,))


def inicializar_cofre(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA_COFRE)


def linha(r) -> dict | None:
    return dict(r) if r is not None else None


def linhas(rs) -> list[dict]:
    return [dict(r) for r in rs]


def verificar_integridade(con: sqlite3.Connection) -> list[str]:
    problemas = []
    r = con.execute("PRAGMA integrity_check").fetchall()
    if not (len(r) == 1 and r[0][0] == "ok"):
        problemas += [f"integrity_check: {x[0]}" for x in r]
    for x in con.execute("PRAGMA foreign_key_check").fetchall():
        problemas.append(f"chave estrangeira inválida: tabela {x[0]}, linha {x[1]} → {x[2]}")
    return problemas


def copiar_banco(origem: sqlite3.Connection, destino: Path) -> None:
    """Cópia consistente (API de backup do SQLite), mesmo com o banco em uso."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    alvo = sqlite3.connect(str(destino))
    try:
        origem.backup(alvo)
        alvo.execute("PRAGMA journal_mode = DELETE")
    finally:
        alvo.close()
