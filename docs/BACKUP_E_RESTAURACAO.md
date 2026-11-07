# Backup e restauração

## O que um backup contém

Cada backup é um arquivo **`.atenabak`** (um ZIP) com:

- `projeto.sqlite` — zona de trabalho: textos anonimizados, códigos, temas, notas, relatórios e **histórico**;
- `cofre.sqlite` — zona protegida, **ainda cifrada** (mapeamento, textos não anonimizados, decisões de anonimização);
- `protegido/*.bin` — arquivos originais, **cifrados**;
- `manifesto.json` — data, tipo, versão do ATENA e o hash SHA-256 de cada arquivo.

Os bancos são copiados com a API de backup do SQLite, que produz uma cópia consistente mesmo com o programa aberto.
Como o cofre continua cifrado, **um backup não expõe dados pessoais** a quem não tiver a senha do cofre (ou o código de
recuperação) **vigente no momento do backup**. A chave da API da OpenAI não faz parte de nenhum backup.

## Backups automáticos

Em **Configurações › Backup e segurança**:
- **Fazer backups automáticos** (ativado por padrão): a cada *N* horas (padrão 24), **somente se o projeto mudou**
  desde o último backup. A verificação roda a cada 15 minutos enquanto o ATENA está aberto.
- **Manter apenas os últimos N automáticos**: padrão **0 = manter todos** (o ATENA nunca apaga backups por conta
  própria). Se você definir, por exemplo, 30, os automáticos mais antigos que excederem 30 serão removidos — os
  manuais nunca.
- **Cópia adicional em**: uma segunda pasta (pendrive, HD externo, pasta do OneDrive/Google Drive). Cada backup é
  copiado também para `<pasta>\ATENA_backups\<projeto>\`. Recomendado: a regra 3-2-1 (3 cópias, 2 mídias, 1 fora de casa).

## Backup manual

- **Um projeto:** *Backup e integridade › Fazer backup agora* (com observação, ex.: “antes de revisar os temas”).
- **Todos os projetos:** *Configurações › Gerar e baixar backup completo* — um único ZIP com os `.atenabak` de todos os
  projetos e as preferências. Útil para trocar de computador.

Os arquivos ficam em `Documentos\ATENA\backups\<identificador do projeto>\` e podem ser baixados pela interface.

## Restaurar

### Pela tela do projeto (backups existentes)
*Backup e integridade › Restaurar* em uma linha da lista:
- **Como novo projeto (recomendado):** cria uma cópia (“… (cópia restaurada em …)”) e mantém o projeto atual intacto.
- **Substituir este projeto:** exige digitar `SUBSTITUIR` e um motivo. Antes de substituir, o ATENA:
  1. cria um backup `pre_restauracao` do estado atual;
  2. move a pasta atual para `Documentos\ATENA\substituidos\<id>-<data>` (nada é apagado);
  3. extrai o backup e registra a restauração no histórico do projeto restaurado, como uma nova operação.

### A partir de um arquivo (outro computador, pendrive)
*Projetos (tela inicial) › Restaurar um backup* › escolha o `.atenabak` › modo › **Restaurar**.

### Depois de restaurar
Desbloqueie o cofre com a **senha que valia na data do backup** (ou com o código de recuperação daquela época).

## Verificação automática ao restaurar

Antes de restaurar, o ATENA confere o manifesto, o hash SHA-256 de cada arquivo, caminhos seguros no ZIP e a
integridade dos dois bancos (`PRAGMA integrity_check`). Um backup corrompido ou alterado é **recusado** sem modificar nada.

## Trocar de computador

1. No computador antigo: *Configurações › Gerar e baixar backup completo*.
2. Instale o ATENA no novo computador.
3. Descompacte o ZIP e restaure cada `.atenabak` (tela inicial › Restaurar um backup).
4. Reconfigure a chave da API, se usar IA (ela é vinculada ao Windows e não é transferida).

## Integridade

*Backup e integridade › Verificar integridade* executa: `integrity_check` e `foreign_key_check` nos dois bancos;
verificação da cadeia de hashes do histórico (detecta alterações feitas fora do ATENA); hash de cada versão de
transcrição; existência de cada original cifrado; correspondência entre fragmentos “ok” e o texto atual.
Se houver problema, faça um backup do estado atual e restaure o último backup íntegro.
