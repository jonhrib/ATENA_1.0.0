# Segurança, privacidade e limitações

## Medidas implementadas

| Ameaça / requisito | Medida |
|---|---|
| Leitura do mapeamento ou dos originais por quem copia a pasta/backup | Cofre com AES-256-GCM; chave derivada da senha (scrypt) e nunca gravada em texto puro; nomes de arquivos originais também cifrados |
| Esquecimento da senha | Código de recuperação exibido uma única vez; troca de senha e geração de novo código |
| Cofre esquecido aberto | Bloqueio automático por inatividade (padrão 20 min) e botão “bloquear” |
| Consulta indevida do mapeamento | Exige senha de novo e motivo; registrada no histórico e no registro de acessos do cofre (append-only) |
| Vazamento em exportações | Exportações comuns leem só a zona de trabalho; testes verificam que nomes do mapeamento não aparecem em XLSX, CSV, JSON, ZIP, DOCX e PDF |
| Envio indevido à IA | Montagem do conteúdo só a partir da zona anonimizada; prévia completa; verificação local de privacidade; bloqueio se houver valor do mapeamento; confirmação dupla; registro integral |
| Chave da API exposta | DPAPI no Windows (vinculada ao usuário); fora do banco, dos backups e das exportações |
| Outro programa/site acessando a interface local | Servidor só em 127.0.0.1; token de inicialização; cookie HttpOnly/SameSite=Strict; CSRF em todo POST; validação do cabeçalho Host (anti DNS-rebinding); CSP restritiva; X-Frame-Options |
| Alteração silenciosa de registros | Histórico, versões de transcrição e de relatório protegidos por gatilhos (sem UPDATE/DELETE); cadeia de hashes verifica adulterações externas |
| Corrupção / queda de energia | Transações, WAL, `synchronous=FULL`, gravação atômica de configurações e backups (`.parcial` → renomeado) |
| Perda de dados | Backups automáticos e manuais, cópia extra opcional, restauração validada, nada é apagado automaticamente |
| Arquivos maliciosos ou inválidos | Lista de extensões aceitas, conferência do conteúdo (PDF/DOCX/ODT/RTF), limite de 50 MB, recusa de binários; HTML importado tem scripts descartados; ZIPs de backup com caminhos verificados |
| Telemetria | Não existe. O ATENA só abre conexões externas quando você envia algo à IA ou testa a conexão |

## Pseudonimização ≠ anonimização irreversível

Enquanto existir o mapeamento (e a senha ou o código de recuperação), os dados são **pseudonimizados**: continuam
sendo dados pessoais para a LGPD, porque podem ser reidentificados. Para tornar a base anonimizada de forma
irreversível ao final da pesquisa, a orientação é: (1) gerar e arquivar, se exigido pelo comitê de ética, a
exportação protegida; (2) esquecer/descartar senha e código de recuperação **ou** apagar manualmente os arquivos
`cofre.sqlite` e a pasta `protegido/` do projeto (e dos backups). O ATENA não faz isso automaticamente, por ser
irreversível.

## Responsabilidades do pesquisador

- Ler integralmente cada documento na revisão da anonimização; a detecção automática é auxiliar.
- Guardar a senha e o código de recuperação; manter o computador com senha de login, disco cifrado (BitLocker) e
  antivírus; não compartilhar a conta do Windows.
- Armazenar backups e exportações protegidas em local seguro e eliminá-los conforme o TCLE/protocolo aprovado.
- Avaliar e informar o uso de IA no protocolo ético; enviar somente o mínimo necessário.
- Revisar e aprovar todo texto produzido com apoio de IA.

## Limitações conhecidas

1. **Detecção de dados pessoais** — baseada em padrões, listas e heurísticas (spaCy opcional). Não reconhece com
   segurança: apelidos sem inicial maiúscula, nomes que são palavras comuns (“Clara”, “Rosa”) no início de frase,
   descrições indiretas, cargos únicos, eventos singulares, cidades pequenas citadas sem contexto, erros de
   transcrição em nomes. A revisão humana é obrigatória por isso.
2. **PDF digitalizado (imagem)** — não há OCR embutido; o ATENA avisa quando quase nada é extraído. Use um OCR
   externo ou cole o texto.
3. **Arquivos de áudio/vídeo** — a transcrição automática (Whisper) do sistema anterior não foi incorporada ao
   executável (exige ffmpeg e modelos de centenas de MB). Importe transcrições prontas (TXT, DOCX, SRT, VTT, CSV).
4. **Um usuário por vez** — o ATENA é monousuário e local. Não há sincronização entre computadores nem edição
   simultânea; use backups para transferir.
5. **Executável não assinado** — o Windows SmartScreen exibe um aviso na primeira execução.
6. **Executável não testado neste ambiente** — o código e os testes foram executados em Linux (Python 3.13);
   o `.exe` é gerado no Windows (localmente ou no GitHub Actions, que roda os testes e um teste de fumaça do
   executável). Os trechos específicos do Windows (DPAPI, abrir pasta no Explorer) não puderam ser executados
   aqui.
7. **Chave da API fora do Windows** — sem `keyring` instalado, fica em arquivo com permissão restrita, apenas
   codificado (não cifrado). Prefira a variável de ambiente nesses sistemas.
8. **Cadeia de hashes** — detecta alterações feitas fora do ATENA, mas alguém com acesso total ao arquivo pode
   reescrever toda a cadeia; ela não substitui backups guardados em outro local.
9. **Frequências** — as contagens das visualizações são descritivas e não indicam relevância temática.
10. **Preços e modelos da OpenAI** — mudam com o tempo; mantenha-os atualizados em Configurações.
