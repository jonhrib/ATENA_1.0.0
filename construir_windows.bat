@echo off
REM Gera o executavel do ATENA (dist\ATENA\ATENA.exe) e, se o Inno Setup estiver instalado, o instalador.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version || (echo Python 3.11+ nao encontrado. Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH". & pause & exit /b 1)
if not exist .venv-build (%PY% -m venv .venv-build)
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller pytest
echo.
echo === Executando os testes automatizados ===
python -m pytest -q --ignore=testes\test_e2e_navegador.py || (echo Testes falharam. Corrija antes de empacotar. & pause & exit /b 1)
echo.
echo === Gerando o executavel ===
pyinstaller empacotamento\ATENA.spec --noconfirm --clean || (pause & exit /b 1)
copy /Y .env.exemplo dist\ATENA\ >nul
xcopy /E /I /Y docs dist\ATENA\docs >nul
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (%ISCC% empacotamento\instalador.iss) else (echo Inno Setup nao encontrado: o instalador nao foi gerado, mas dist\ATENA\ATENA.exe esta pronto.)
echo.
echo Pronto. Executavel: dist\ATENA\ATENA.exe
pause
