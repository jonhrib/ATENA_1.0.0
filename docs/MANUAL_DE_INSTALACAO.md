# Manual de instalação do ATENA (Windows 10 e 11)

Há três formas de usar o ATENA. Para o uso cotidiano, a recomendada é a **1 (instalador)** ou a **2 (executável)**.
Todas guardam os dados no mesmo lugar (`Documentos\ATENA`), então você pode trocar de forma sem perder nada.

---

## 1. Gerar o executável e o instalador sem instalar nada (GitHub Actions)

Esta é a forma mais simples de obter o `ATENA.exe` sem configurar Python no seu computador. O GitHub compila
o programa num computador Windows na nuvem, executa os testes automatizados e entrega um ZIP pronto.

1. Crie uma conta gratuita em <https://github.com> (se ainda não tiver).
2. Clique em **New repository**, dê um nome (ex.: `atena`), marque **Private** e clique em **Create repository**.
3. Na página do repositório, clique em **uploading an existing file** e arraste **todo o conteúdo** da pasta do
   ATENA (descompactada do ZIP), incluindo a pasta oculta `.github`. Clique em **Commit changes**.
   - Se a pasta `.github` não aparecer no Explorador, ative *Exibir › Itens ocultos*.
4. Abra a aba **Actions** → **Gerar ATENA para Windows** → **Run workflow** → **Run workflow**.
5. Aguarde (cerca de 5–10 minutos). Quando ficar verde, clique na execução e baixe o artefato **ATENA-windows**.
6. Descompacte. Você terá:
   - `dist/ATENA_Instalador_1.0.0.exe` — instalador (cria atalho no menu Iniciar e na Área de Trabalho);
   - `dist/ATENA/ATENA.exe` — versão portátil (não precisa instalar).

> Os dados da sua pesquisa **não** vão para o GitHub: o repositório contém só o código. Nunca envie
> a pasta `Documentos\ATENA` para lá.

## 2. Gerar o executável no seu próprio computador

1. Instale o **Python 3.12** em <https://www.python.org/downloads/windows/>. Na primeira tela do instalador,
   marque **“Add python.exe to PATH”** e clique em *Install Now*.
2. (Opcional, para gerar também o instalador) instale o **Inno Setup 6**: <https://jrsoftware.org/isdl.php>.
3. Descompacte o ZIP do ATENA numa pasta, por exemplo `C:\ATENA-fonte`.
4. Dê dois cliques em **`construir_windows.bat`**. O script:
   - cria um ambiente Python isolado (`.venv-build`);
   - instala as dependências (precisa de internet nesta etapa);
   - **executa os testes automatizados** e interrompe se algum falhar;
   - gera `dist\ATENA\ATENA.exe` com o PyInstaller;
   - se o Inno Setup estiver instalado, gera `dist\ATENA_Instalador_1.0.0.exe`.
5. Execute o instalador **ou** copie a pasta `dist\ATENA` para onde quiser e crie um atalho para `ATENA.exe`.

## 3. Executar a partir do código-fonte (sem gerar executável)

1. Instale o Python 3.11 ou superior (marcando *Add python.exe to PATH*).
2. Dê dois cliques em **`Iniciar_ATENA.bat`**.
   Na primeira vez ele cria a pasta `.venv` e instala as dependências (requer internet uma única vez).
   Nas próximas vezes, abre direto, sem internet.

No Linux/macOS: `./iniciar_atena.sh`.

---

## Primeiro uso

1. Ao abrir, uma janela preta (console) mostra “ATENA … em execução” e o navegador abre a interface.
   **Não feche a janela preta enquanto usar o ATENA** — ela é o programa. Para sair, use o botão
   **Encerrar** no canto superior direito da interface (ou feche a janela preta).
2. A interface roda em `http://127.0.0.1:<porta>` — um endereço que só existe no seu computador.
   Se você abrir o endereço sem passar pelo atalho, aparecerá “Abra o ATENA pelo atalho”: isso é proposital
   (proteção contra acesso de outros programas).
3. Se o ATENA já estiver aberto e você clicar no atalho de novo, ele apenas reabre a janela existente.
4. O Windows Defender/SmartScreen pode avisar que o executável “não é reconhecido”, porque ele não é assinado
   digitalmente. Clique em **Mais informações › Executar assim mesmo**. (Para eliminar o aviso seria necessário
   um certificado de assinatura de código, que é pago.)

## Onde ficam os dados

| O quê | Local padrão |
|---|---|
| Projetos (bancos SQLite, cofre, originais cifrados) | `Documentos\ATENA\projetos\<identificador>\` |
| Backups | `Documentos\ATENA\backups\` |
| Exportações | `Documentos\ATENA\exportacoes\` |
| Registros de execução (logs) | `Documentos\ATENA\logs\atena.log` |
| Preferências | `Documentos\ATENA\configuracoes.json` |
| Chave da API (protegida pelo Windows) | `Documentos\ATENA\segredos.bin` |
| Projetos substituídos numa restauração | `Documentos\ATENA\substituidos\` |

Para usar outra pasta: copie `.env.exemplo` para `atena.env` (ao lado do `ATENA.exe`) e defina
`ATENA_DADOS=D:\MinhaPasta`. Para levar tudo num pendrive (modo portátil), crie um arquivo vazio
`modo_portatil.txt` ao lado do `ATENA.exe`: os dados ficarão em `ATENA_dados`, na mesma pasta.

## Atualizar para uma nova versão

Instale/copie a nova versão por cima da anterior. Os dados em `Documentos\ATENA` não são alterados pela
instalação nem pela desinstalação. Faça um backup antes de atualizar (Configurações › Backup completo).

## Desinstalar

Painel de Controle › Programas › ATENA › Desinstalar. Os dados da pesquisa **permanecem** em
`Documentos\ATENA`; apague essa pasta manualmente apenas se tiver certeza.

## Requisitos

- Windows 10 ou 11 (64 bits); 4 GB de RAM; ~300 MB livres para o programa, mais o espaço dos seus dados.
- Navegador atualizado (Edge, Chrome ou Firefox). O Edge já vem no Windows.
- Internet: **não** é necessária para usar o ATENA. Só é usada se você ativar a integração com a OpenAI
  (e, uma única vez, para instalar dependências se for executar a partir do código-fonte).
