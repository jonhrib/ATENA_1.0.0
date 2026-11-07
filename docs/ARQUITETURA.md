# Arquitetura do ATENA

## 1. Inspeção do sistema anterior e o que foi reaproveitado

O ponto de partida foi o sistema de linha de comando do TCC (`Desenvolvimento/`, ~5.600 linhas): `revisar.py`,
`anonimizar.py` (spaCy + confirmação S/N), `criptografia.py` (Fernet, chave fora do projeto), `analisar.py` e o
pacote `analise/` (livro de códigos, temas candidato→revisão→definido, trilha `decisoes`). Ele não tinha interface
gráfica, versões de texto, fragmentos por seleção, histórico append-only, backup/restauração nem integração com IA,
e a pasta `testes/` citada no README não estava no ZIP.

| Aproveitado | Onde está agora |
|---|---|
| Formato de transcrição Scribbl `Locutor - hh:mm:` (parser de turnos) | `atena/textos.py` (`turnos`, `rotulos_locutor`) |
| Lista de entidades preservadas (IES e Empresas Juniores do PR) | `atena/recursos/listas_protegidas/` |
| Padrões fixos de dados identificadores (e-mail, telefone, CPF, URL, @) | ampliados em `servicos/pii.py` |
| Códigos `PARTICIPANTE_XXX`/`PESSOA_XXX` únicos no projeto; substituição que diferencia maiúsculas | `servicos/anonimizacao.py`, `servicos/protegido.py` |
| Princípio “sugestão só vale com aceite” e trilha de decisões | `servicos/ia.py`, `auditoria.py` |
| Ciclo de temas e revisão em dois níveis | `servicos/temas.py` |
| Transcrições processadas, ficha de participantes, mapeamento Fernet | importador `servicos/legado.py` |

O spaCy passou a ser **opcional** (é usado se estiver instalado): ele ocupa ~500 MB, dificultaria o executável e,
nos testes do sistema anterior, gerava muitos falsos positivos. A detecção padrão combina mapeamento, padrões,
rótulos de locutor, contexto e uma heurística com lista de prenomes/sobrenomes frequentes.

## 2. Decisão de interface: Flask local em vez de Streamlit

| Requisito | Streamlit | Flask + HTML/JS (escolhido) |
|---|---|---|
| Selecionar fragmentos com o mouse e saber a posição exata no texto | exige componente customizado em React | nativo (Range API do navegador) |
| Mapa temático com arrastar e soltar | exige componente customizado | Cytoscape.js embutido |
| Empacotamento com PyInstaller | problemático (servidor Tornado, muitos arquivos dinâmicos) | simples |
| Modelo de execução | reexecuta o script a cada interação | requisições pontuais, transações explícitas |
| Testes automatizados de interface | limitados | cliente de teste do Flask + Playwright |

A interface é servida em `127.0.0.1` e aberta no navegador padrão. Nenhum recurso externo é carregado
(Cytoscape.js vai junto com o programa); a Content-Security-Policy bloqueia scripts de terceiros.

## 3. Organização do código

```
atena/
  __main__.py      inicialização: porta livre, token de acesso, logs, backups automáticos, navegador
  config.py        pasta de dados, preferências (configuracoes.json), atena.env
  db.py            esquemas SQLite, conexão (WAL, synchronous=FULL, foreign_keys), transações com SAVEPOINT
  auditoria.py     histórico append-only com cadeia de hashes; consulta e verificação
  cofre.py         criptografia (AES-256-GCM, scrypt, envelope), gerenciador de cofres abertos
  segredos.py      chave da API (DPAPI no Windows; keyring/arquivo em outros sistemas)
  textos.py        normalização, turnos de fala, diff e realocação de posições
  servicos/
    projetos.py participantes.py documentos.py importacao.py protegido.py
    pii.py anonimizacao.py transcricoes.py codificacao.py temas.py fases.py notas.py
    relatorios.py exportacao.py backup.py restauracao.py painel.py visualizacoes.py
    ia.py legado.py demo.py
  web/
    app.py rotas.py templates/ static/{css,js,vendor}
  recursos/        listas (termos protegidos, prenomes, não-nomes, palavras vazias)
```

As regras ficam na camada `servicos/`; a camada `web/` só traduz requisições. Assim, os testes exercitam as regras
diretamente e também pela interface.

## 4. Armazenamento

Cada projeto é uma pasta independente com **dois bancos SQLite**:

```
projetos/<uid>/
  projeto.sqlite   zona de trabalho (somente conteúdo anonimizado + análise + histórico)
  cofre.sqlite     zona protegida (valores identificáveis sempre cifrados)
  protegido/       originais cifrados (DOC_001-xxxx.bin)
```

**projeto.sqlite**: `projeto`, `termos`, `participantes`, `documentos`, `transcricao_versoes` (imutável),
`fragmentos`, `codigos`, `codificacoes`, `temas` (com `pai_id` para subtemas), `tema_codigos`, `tema_evidencias`,
`revisoes_tema`, `notas`, `leituras`, `decisoes`, `fases`, `relatorios`, `relatorio_versoes` (imutável),
`ia_interacoes`, `sugestoes`, `exportacoes`, `historico` (append-only).

**cofre.sqlite**: `cofre_meta` (chave embrulhada pela senha e pelo código de recuperação), `originais`, `textos`
(extraído/revisado, imutáveis), `identidades` (mapeamento reversível), `contadores`, `sessoes` e `itens` de
anonimização, `acessos` (append-only).

Cadeia de rastreabilidade: `participante → documento → versão anonimizada → fragmento (posição + texto) →
codificação → código → tema/subtema → evidência representativa → citação [[frag:N]] no relatório`.

Persistência: toda operação acontece numa transação (`BEGIN IMMEDIATE`) que grava a alteração **e** o registro de
histórico juntos; `journal_mode=WAL` e `synchronous=FULL` garantem que nada se perde ao fechar o programa ou
desligar o computador depois de uma operação concluída.

## 5. Criptografia

- Chave de dados (DEK) aleatória de 256 bits; conteúdo cifrado com **AES-256-GCM** (nonce aleatório, dado
  associado indicando o contexto — impede trocar um valor cifrado de lugar).
- A DEK é guardada “embrulhada” duas vezes: com uma chave derivada da **senha** (scrypt, N=2¹⁵, r=8, p=1, sal
  aleatório) e com uma chave derivada do **código de recuperação** (24 caracteres aleatórios).
- A DEK aberta só existe na memória do processo e expira após o tempo de inatividade configurado.
- Exportação protegida: mesmo esquema, com senha própria de exportação (`.atenaseg`).

## 6. Fragmentos e edição de transcrições

Um fragmento guarda `inicio`, `fim`, o **texto exato**, 40 caracteres de contexto antes e depois e a versão em que foi
criado. Ao salvar uma nova versão, o ATENA calcula as diferenças (por linha e, dentro das linhas alteradas, por
caractere), converte as posições e classifica cada fragmento como `ok`, `realocado` (texto reencontrado em outra
posição) ou `inconsistente` (texto alterado/removido). O texto original do fragmento é sempre preservado até a
revisão do pesquisador, que é registrada no histórico.

## 7. Integração com IA

Chamada HTTP direta (`urllib`, sem SDK) a `POST /v1/chat/completions` com `response_format=json_object` e
`store=false`. Parâmetros recusados por algum modelo são retirados e a requisição é repetida com o **mesmo
conteúdo autorizado**. A resposta é validada (ids, citações literais) antes de virar sugestão pendente. Os testes
usam um servidor local que imita a API — nenhum dado sai da máquina durante os testes.

## 8. Empacotamento

`PyInstaller` (modo pasta, `console=True` para mostrar que o programa está em execução) + `Inno Setup`
(instalação por usuário, sem exigir administrador, atalhos no menu Iniciar e na Área de Trabalho). O workflow
`.github/workflows/windows.yml` compila no Windows da nuvem do GitHub, roda os testes e um teste de fumaça do
`.exe` e publica o ZIP.
