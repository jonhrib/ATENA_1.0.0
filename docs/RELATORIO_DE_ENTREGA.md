# Relatório de entrega — ATENA 1.0.0

Data: 06/11/2026. Código: ~8.400 linhas Python, ~2.300 linhas de interface (HTML/CSS/JS), ~1.500 linhas de testes.
Resultado da última execução dos testes: **107 aprovados, 0 falhas, 0 ignorados** (`docs/RESULTADO_DOS_TESTES.txt`).

## 1. Critérios de aceitação (seção 17 do pedido)

Todos os 20 passos são executados de ponta a ponta, em sequência, pelo teste
`testes/test_aceitacao.py::test_fluxo_completo_de_ponta_a_ponta`, usando a interface HTTP do ATENA, **com conexões
externas bloqueadas e sem chave de API**. Os passos com interação de mouse (seleção de trechos, diálogos, mapa) são
executados num navegador real pelo teste `testes/test_e2e_navegador.py`.

| # | Critério | Como foi verificado |
|---|---|---|
| 1 | Criar projeto | formulário `/projetos/novo` (aceitação); `test_cofre`, `test_auditoria` |
| 2 | Importar entrevista | upload TXT (aceitação); 8 formatos em `test_importacao` |
| 3 | Identificar informações pessoais | nome, e-mail, celular, terceiro citado detectados (aceitação); `test_pii` (9 testes) |
| 4 | Revisar e aprovar | lote + decisões item a item (aceitação); manual, manter, rejeitar, ajustar (`test_anonimizacao`); no navegador |
| 5 | Gerar versão anonimizada | nenhum nome/e-mail/telefone resta; termos protegidos permanecem |
| 6 | Preservar original e mapeamento protegido | download do original idêntico byte a byte; mapeamento só com senha; DEK e nomes ausentes dos arquivos (`test_cofre`) |
| 7 | Editar transcrição | nova versão com observação; realocação de fragmentos (`test_transcricoes_codificacao`) |
| 8 | Selecionar fragmento | API de fragmentos (aceitação); seleção com o mouse no navegador |
| 9 | Criar código associado | idem; vários códigos por fragmento |
| 10 | Consultar fragmentos do código | página do código lista evidências com participante |
| 11 | Agrupar códigos em temas | criação de tema com códigos; mapa interativo |
| 12 | Revisar temas | revisões nível 1 e 2 → “revisado” |
| 13 | Definir e nomear | confirmação com evidência representativa |
| 14 | Registrar justificativas | justificativas presentes no histórico |
| 15 | Relatório com evidências | rascunho com citação vinculada (fragmento nº) |
| 16 | Histórico de todas as etapas | operações de todas as etapas presentes; “cadeia íntegra” |
| 17 | Exportar | XLSX e DOCX válidos; todos os conjuntos em CSV/JSON/ZIP; DOCX/PDF/MD/HTML de relatórios |
| 18 | Fechar e reabrir sem perder dados | nova instância da aplicação lê tudo; `test_backup::test_persistencia…` |
| 19 | Backup e restauração | backup pela interface e restauração como novo projeto; substituição preservando o anterior |
| 20 | Sem internet e sem API | sockets externos bloqueados e chave ausente durante todo o fluxo |

## 2. Funcionalidades implementadas, por módulo do pedido

- **Arquitetura local (seção 2):** Python + Flask (interface no navegador, só em 127.0.0.1), SQLite, python-docx,
  pypdf (PyMuPDF opcional), openpyxl, reportlab, Cytoscape.js embutido. Sem nuvem, sem telemetria. Executável via
  PyInstaller + instalador Inno Setup. *Desvios justificados:* Flask em vez de Streamlit; sem Pandas/Plotly/SQLAlchemy
  (ver `ARQUITETURA.md` §2).
- **Importação e anonimização (seção 3):** 11 formatos; validação de conteúdo; detecção local (mapeamento, padrões
  com CPF validado, rótulos de locutor, contexto de local, heurística de nomes, spaCy opcional, verificação por IA de
  texto já anonimizado); revisão humana com aprovar/rejeitar/manter, correção de trecho, inclusão manual, decisão por
  ocorrência, código definido pelo pesquisador; termos protegidos e exceções; códigos únicos e consistentes no
  projeto; mapeamento reversível cifrado, separado, com consulta autorizada e registrada; nunca enviado à IA nem
  exportado em formato comum; quatro versões preservadas de cada documento.
- **Projetos e participantes (seção 4):** todos os campos pedidos; criar, editar, arquivar, exportar, backup e
  restaurar; participantes com metadados não identificáveis configuráveis e situação da entrevista.
- **Editor (seção 5):** visualizar, editar, salvar, versões, comparação, restauração, observação por alteração,
  busca com navegação, trechos codificados destacados, seleção → codificação, realocação e sinalização de
  fragmentos inconsistentes, proteção contra salvar sobre versão mais nova.
- **Análise temática (seção 6):** as seis fases, com conclusão/reabertura pelo pesquisador; fase 1 (leitura,
  diário, ideias, padrões, relevantes, leituras); fase 2 (códigos com todos os campos e histórico, exclusão
  reversível, mesclagem, comparação entre entrevistas, sugestões de IA com aprovação); fase 3 (temas, subtemas,
  mapa arrastável, coocorrência, códigos sem tema, justificativas); fase 4 (revisão nível 1/2, sobreposições,
  fundir, dividir, descartar, reorganizar, confronto com entrevistas); fase 5 (todos os campos pedidos, evidências
  representativas, confirmação, alerta de definição desatualizada); fase 6 (relatório final e metodológico,
  quadro de temas e códigos, evidências, comparação entre entrevistas, histórico, versões e aprovação).
- **IA (seção 7):** 9 operações; prévia com conteúdo, finalidade, verificação de privacidade e confirmação;
  bloqueio por valores do mapeamento; registro de data, operação, modelo, registros, finalidade, resposta, tokens,
  custo e decisão; validação contra citações inventadas; chave fora do banco (DPAPI); tela de configuração com
  teste de conexão; limite mensal; tutorial completo.
- **Histórico (seção 8):** append-only com gatilhos e cadeia de hashes; campos pedidos; busca e filtros;
  comparação antes/depois; restauração como nova operação; backups automáticos (frequência configurável) e manuais.
- **Painel (seção 9), visualizações (seção 10), exportação (seção 11), interface (seção 12), segurança (seção 13):**
  conforme `MANUAL_DE_UTILIZACAO.md` e `SEGURANCA_PRIVACIDADE_E_LIMITACOES.md`.
- **Migração:** importador do sistema anterior (transcrições processadas, ficha, termos protegidos e mapeamento
  Fernet recifrado).

## 3. O que foi efetivamente testado (107 testes)

| Arquivo | Testes | Cobertura |
|---|---|---|
| `test_aceitacao.py` | 1 | os 20 critérios, offline, sem API |
| `test_e2e_navegador.py` | 1 | Chromium real: demonstração, anonimização com seleção manual, aplicação, codificação por seleção, 2º código, evidências, tema, mapa, relatório, histórico — sem erros de JavaScript |
| `test_web_seguranca.py` | 6 | token, Host, CSRF, cabeçalhos, 39 páginas abrindo com CSRF preenchido, redirecionamento do cofre |
| `test_cofre.py` | 8 | senha, recuperação, troca, bloqueio, DEK e nomes ausentes dos arquivos, mapeamento, registro de acessos |
| `test_importacao.py` | 17 | TXT (UTF-8 e Windows-1252), MD, DOCX, PDF, ODT, HTML, RTF, CSV, SRT; arquivos inválidos; original cifrado e idêntico; nome original fora do banco de trabalho; versões revisadas |
| `test_pii.py` | 9 | padrões, CPF, nomes e falsos positivos, rótulos, termos protegidos, exceções, mapeamento sem acento, locais, sobreposição |
| `test_anonimizacao.py` | 8 | fluxo completo, pendências bloqueiam, consistência de códigos entre documentos, manual/manter/rejeitar, termo protegido, ajuste, reanonimização com realocação |
| `test_transcricoes_codificacao.py` | 8 | multicódigo, seleção desatualizada, realocação ok/realocado/inconsistente, versões imutáveis, restauração, conflito de versão, busca, CRUD/mesclagem de códigos |
| `test_temas_fases.py` | 5 | ciclo completo do tema, subtema/fusão/divisão/descarte, evidências, fases, notas |
| `test_auditoria.py` | 5 | append-only, adulteração detectada, campos do registro, restauração pelo histórico, arquivamento |
| `test_relatorios_exportacao.py` | 22 | rascunhos, verificação de citações, aprovação, todos os conjuntos em CSV, XLSX/JSON/ZIP sem nomes reais, DOCX/PDF/MD/HTML, exportação protegida |
| `test_backup.py` | 6 | persistência, restauração como cópia e substituição, backup corrompido, automático só com alterações, backup completo |
| `test_ia.py` | 10 | sem chave, desativada, teste de conexão, sugestão de códigos com citação inventada descartada, bloqueio por mapeamento, ausência de identidades no envio, síntese com referências inválidas, limite mensal, chave fora do banco, modelo sem modo JSON |
| `test_legado.py` | 1 | importação do sistema anterior (pasta fictícia no mesmo formato) |

A integração com a OpenAI foi testada contra um **servidor local que imita a API**; nenhuma chamada real foi feita
(não há chave nem acesso à internet neste ambiente).

## 4. O que NÃO foi possível executar aqui (e como verificar)

1. **Gerar e rodar o `ATENA.exe`** — o ambiente de desenvolvimento é Linux, sem acesso ao PyPI; o PyInstaller não
   compila executáveis Windows a partir do Linux. Entregues: `empacotamento/ATENA.spec`, `construir_windows.bat`
   (roda os testes antes de empacotar), `empacotamento/instalador.iss` e o workflow do GitHub Actions, que compila
   no Windows, roda os testes e um teste de fumaça do `.exe`. **Ação sua:** seguir o Manual de instalação, opção 1 ou 2.
2. **DPAPI do Windows** e **“abrir pasta no Explorer”** — código específico do Windows, não executável em Linux.
3. **Chamada real à API da OpenAI** — exige sua chave. **Ação sua:** Configurações › Testar conexão.
4. **spaCy e PyMuPDF** — caminhos opcionais, não instalados aqui (o ATENA usa pypdf/pdfplumber).

## 5. Limitações

Ver `SEGURANCA_PRIVACIDADE_E_LIMITACOES.md` (detecção automática imperfeita, sem OCR, sem transcrição de áudio,
monousuário, executável não assinado, cadeia de hashes não substitui backup externo).

## 6. Seus dados reais

As transcrições reais enviadas no ZIP (`transcricoes/brutas`, `revisadas`, `processadas`) **não** foram usadas em
testes, demonstrações nem incluídas no pacote do ATENA. Para trazê-las: *Documentos › Importar do sistema anterior*
(ZIP da pasta `Desenvolvimento` e, se quiser o mapeamento, o arquivo `chave.key`).
