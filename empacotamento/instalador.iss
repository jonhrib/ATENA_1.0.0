; Instalador do ATENA (Inno Setup 6 — https://jrsoftware.org/isinfo.php)
; Compile depois de gerar dist\ATENA com o PyInstaller:  iscc empacotamento\instalador.iss
#define Versao "1.0.0"
[Setup]
AppId={{6B0D5A2E-1C4F-4C1B-9E9A-8D1A7E2B3C40}
AppName=ATENA
AppVersion={#Versao}
AppPublisher=ATENA
DefaultDirName={localappdata}\Programs\ATENA
DefaultGroupName=ATENA
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ATENA_Instalador_{#Versao}
SetupIconFile=atena.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ATENA.exe
; Os dados da pesquisa ficam em Documentos\ATENA e NÃO são removidos na desinstalação.

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\ATENA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\.env.exemplo"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ATENA"; Filename: "{app}\ATENA.exe"
Name: "{group}\Manual do ATENA"; Filename: "{app}\docs\MANUAL_DE_UTILIZACAO.md"
Name: "{group}\Desinstalar o ATENA"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ATENA"; Filename: "{app}\ATENA.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ATENA.exe"; Description: "Abrir o ATENA agora"; Flags: nowait postinstall skipifsilent
