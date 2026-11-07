@echo off
REM Executa o ATENA a partir do codigo-fonte (sem gerar executavel).
REM Na primeira vez cria um ambiente Python isolado e instala as dependencias (requer internet uma unica vez).
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo Preparando o ATENA pela primeira vez...
  %PY% -m venv .venv || (echo Python 3.11+ nao encontrado. Instale em https://www.python.org/downloads/ & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt || (echo Falha ao instalar dependencias. & pause & exit /b 1)
)
.venv\Scripts\python.exe -m atena %*
if errorlevel 1 pause
