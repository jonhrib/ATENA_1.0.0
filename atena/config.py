"""Local dos dados e configurações gerais do ATENA.

Ordem de escolha da pasta de dados:

1. variável de ambiente ``ATENA_DADOS`` (usada pelos testes);
2. modo portátil: se existir um arquivo ``modo_portatil.txt`` ao lado do
   executável (ou do iniciar.py), os dados ficam em ``ATENA_dados`` nessa pasta;
3. Windows: ``Documentos\\ATENA`` do usuário;
4. outros sistemas: ``~/ATENA``.

Estrutura da pasta de dados::

    ATENA/
      configuracoes.json        preferências gerais (sem segredos)
      segredos.bin              chave da API protegida (DPAPI no Windows)
      projetos/<uid>/
          projeto.sqlite        zona de trabalho: textos anonimizados, análise, histórico
          cofre.sqlite          zona protegida: mapeamento, textos originais (cifrados)
          protegido/            arquivos originais cifrados
      backups/                  backups manuais e automáticos
      exportacoes/              arquivos exportados
      logs/                     registros de execução
      substituidos/             projetos substituídos numa restauração (nunca apagados)
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

_TRAVA = threading.Lock()


def pasta_programa() -> Path:
    """Pasta do executável (PyInstaller) ou da raiz do código-fonte."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def pasta_recursos() -> Path:
    """Arquivos somente leitura que acompanham o programa (listas, modelos)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "atena" / "recursos"


def carregar_ambiente() -> None:
    """Lê ``atena.env`` (CHAVE=valor por linha) ao lado do programa, sem sobrescrever variáveis existentes."""
    arq = pasta_programa() / "atena.env"
    if not arq.is_file():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


def pasta_dados() -> Path:
    if os.environ.get("ATENA_DADOS"):
        p = Path(os.environ["ATENA_DADOS"]).expanduser()
    elif (pasta_programa() / "modo_portatil.txt").exists():
        p = pasta_programa() / "ATENA_dados"
    elif os.name == "nt":
        docs = Path.home() / "Documents"
        p = (docs if docs.exists() else Path(os.environ.get("LOCALAPPDATA", Path.home()))) / "ATENA"
    else:
        p = Path.home() / "ATENA"
    p = p.resolve()
    for sub in ("projetos", "backups", "exportacoes", "logs", "substituidos"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def pasta_projetos() -> Path:
    return pasta_dados() / "projetos"


def pasta_backups() -> Path:
    return pasta_dados() / "backups"


def pasta_exportacoes() -> Path:
    return pasta_dados() / "exportacoes"


def pasta_logs() -> Path:
    return pasta_dados() / "logs"


PADRAO = {
    "backup_automatico": True,
    "backup_intervalo_horas": 24,
    # 0 = manter todos os backups automáticos (nada é apagado sem pedido).
    "backup_reter_ultimos": 0,
    "backup_pasta": "",
    "cofre_bloqueio_minutos": 20,
    "ia_habilitada": False,
    "ia_modelo": "gpt-6-luna",
    "ia_url_base": "https://api.openai.com/v1",
    "ia_temperatura": 0.2,
    "ia_tempo_limite_s": 90,
    # Preços em US$ por 1 milhão de tokens do modelo padrão (gpt-6-luna, conforme a página oficial
    # developers.openai.com/api/docs/pricing consultada em nov./2026). Confira e atualize em Configurações.
    "ia_preco_entrada": 0.10,
    "ia_preco_saida": 0.50,
    "ia_limite_mensal_usd": 5.0,
    "ia_max_caracteres_envio": 24000,
    "porta": 0,
    "abrir_navegador": True,
}


def arquivo_configuracoes() -> Path:
    return pasta_dados() / "configuracoes.json"


def carregar() -> dict:
    valores = dict(PADRAO)
    arq = arquivo_configuracoes()
    if arq.is_file():
        try:
            valores.update(json.loads(arq.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return valores


def salvar(alteracoes: dict) -> dict:
    with _TRAVA:
        valores = carregar()
        for k, v in alteracoes.items():
            if k in PADRAO:
                valores[k] = v
        arq = arquivo_configuracoes()
        tmp = arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(valores, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, arq)
        return valores


def obter(chave: str):
    return carregar().get(chave, PADRAO.get(chave))
