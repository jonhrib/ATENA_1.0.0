# Tutorial — configurar a API da OpenAI no ATENA

A integração é **opcional**. Sem ela, todas as funções do ATENA continuam disponíveis. Este tutorial parte do
zero: criar a conta, gerar a chave, guardá-la com segurança, testar, usar e acompanhar os custos.

> Informações sobre modelos, preços e telas da OpenAI conferidas na documentação oficial em novembro de 2026.
> A OpenAI muda nomes de modelos e preços com frequência: confira sempre os links indicados.

## 1. O que é a chave da API e quanto custa

A **API** é o acesso programático aos modelos da OpenAI, **separado da assinatura do ChatGPT** (ter ChatGPT Plus
não dá crédito na API). A cobrança é por **tokens** (pedaços de palavras; em português, ~1 token ≈ 3–4 caracteres),
somando o texto enviado (entrada) e o recebido (saída).

O ATENA vem configurado com o modelo **`gpt-6-luna`**, que a OpenAI apresenta como o mais econômico para
tarefas focadas e de alto volume, a **US$ 0,10 por milhão de tokens de entrada e US$ 0,50 por milhão de saída**
(página oficial de preços). Exemplo: pedir sugestões de códigos para um trecho de ~6.000 caracteres custa algo
como 2.000 tokens de entrada + 1.000 de saída ≈ **US$ 0,0007** (menos de um décimo de centavo de dólar). Uma
alternativa mais capaz é o `gpt-6.1-terra` (US$ 1,00 / US$ 5,00 por milhão).

Modelos antigos, como `gpt-4.1-mini` e `gpt-4o-mini`, estão **descontinuados** e deixam de funcionar em
14/01/2027, segundo a página de descontinuações da OpenAI.

## 2. Criar a conta e adicionar crédito

1. Acesse <https://platform.openai.com> e clique em **Sign up** (ou entre com sua conta existente).
2. Vá em **Billing** (<https://platform.openai.com/account/billing/overview>) e adicione um método de pagamento e
   **crédito pré-pago**. Para uso no ATENA, US$ 5 costumam durar muito.
3. **Defina um limite de gastos** (recomendado): em **Organization limits**
   (<https://platform.openai.com/settings/organization/limits>), seção **Spend**, clique em **Edit spend limit**,
   informe o **Monthly spend limit**, ative **Enforce a hard limit** e salve. Ao atingir o limite, a API passa a
   recusar as requisições (o ATENA mostra o erro 429 com essa explicação).

## 3. Gerar a chave

1. Acesse **API keys**: <https://platform.openai.com/api-keys>.
2. Clique em **Create new secret key**, dê um nome (ex.: `ATENA – meu notebook`) e confirme.
3. **Copie a chave** (começa com `sk-`). Ela é exibida **uma única vez**. Não a envie por e-mail, não a cole em
   documentos e não a coloque no código.

## 4. Guardar a chave no ATENA com segurança

1. Abra o ATENA › **Configurações** › cartão **Chave da API**.
2. Cole a chave em “Colar nova chave” e clique em **Guardar chave**.
   - No **Windows**, a chave é cifrada com o **DPAPI** do próprio Windows, vinculada à sua conta de usuário, e
     gravada em `Documentos\ATENA\segredos.bin`. Outro usuário ou outro computador não consegue decifrá-la.
   - A chave **nunca** vai para os bancos de dados, para os backups ou para as exportações.
   - Alternativa: definir a variável de ambiente `OPENAI_API_KEY` (ou a linha `OPENAI_API_KEY=` no arquivo
     `atena.env`). Ela tem prioridade sobre a chave guardada.
3. No cartão **Inteligência artificial**, marque **Ativar a integração**, confira o modelo (`gpt-6-luna`), os
   preços e o **limite mensal** interno do ATENA (padrão US$ 5,00) e clique em **Salvar**.
4. Clique em **Testar conexão**. O teste só lista os modelos disponíveis na sua conta (não envia nenhum dado da
   pesquisa) e avisa se o modelo configurado não estiver na lista.

Para remover a chave deste computador: **Configurações › Remover chave**. Se suspeitar de vazamento, **revogue**
a chave em <https://platform.openai.com/api-keys> e gere outra.

## 5. Como cada envio funciona (e o que é protegido)

1. Você clica numa ação de IA (ex.: selecionar um trecho no editor › “✦ Sugerir códigos (IA)”).
2. O ATENA **prepara** o envio e abre a **prévia**, que mostra:
   - a **finalidade** (ex.: “Sugerir códigos iniciais para trechos selecionados”);
   - o **conteúdo exato** que será enviado (instrução + material em JSON);
   - a **verificação de privacidade** local (padrões de dados pessoais e, com o cofre aberto, comparação com
     todas as identidades do mapeamento);
   - as caixas de **confirmação** (“conteúdo anonimizado” e “autorizo o envio”).
3. Se algum valor do mapeamento aparecer no conteúdo, o envio é **bloqueado**. Alertas de alta confiança (e-mail,
   telefone…) exigem uma confirmação adicional.
4. Só depois de você clicar em **Enviar** a requisição sai (com `store: false`, pedindo que a conversa não seja
   armazenada na plataforma).
5. A resposta vira **sugestões pendentes**. O ATENA confere as citações: trechos que não existem literalmente no
   material enviado e referências a fragmentos não enviados são **descartados e sinalizados**.
6. Você **aceita**, **edita e aceita** ou **rejeita** cada sugestão. Tudo fica no histórico e em
   *Inteligência artificial › Registro de envios* (data, operação, modelo, registros enviados, finalidade,
   resposta, tokens, custo e decisão).

O que **nunca** é enviado: o mapeamento de identidades, os arquivos originais, os textos não anonimizados e a
chave do cofre — o código que monta o conteúdo só lê a zona de trabalho (anonimizada). Prefira enviar **trechos**
e fragmentos, não entrevistas inteiras (o ATENA limita cada envio a 24.000 caracteres, configurável).

Segundo a documentação da OpenAI, os dados enviados pela API **não são usados para treinar modelos** por padrão,
mas podem ficar em registros de monitoramento de abuso por **até 30 dias**. Informe isso no seu protocolo ético
(TCLE/CEP) se for usar IA, e considere enviar somente trechos já anonimizados e revisados.

## 6. Acompanhar os custos

- **No ATENA:** *Inteligência artificial* mostra o gasto estimado do mês (calculado com os tokens informados
  pela API e os preços configurados) e cada envio individual. O ATENA recusa envios que ultrapassariam o limite
  mensal configurado.
- **Na OpenAI (valor oficial):** <https://platform.openai.com/settings/organization/usage>.
- Se a OpenAI alterar os preços, atualize os campos de preço em *Configurações* para manter a estimativa correta.

## 7. Problemas comuns

| Mensagem | Causa provável | O que fazer |
|---|---|---|
| “Nenhuma chave configurada” | chave não guardada | Configurações › Guardar chave |
| Erro 401 | chave inválida ou revogada | gere nova chave |
| Erro 404 / “modelo não encontrado” | nome do modelo errado ou indisponível na conta | use “Testar conexão” e escolha um modelo da lista |
| Erro 429 | sem crédito, limite de gastos atingido ou excesso de requisições | confira Billing e Limits; aguarde e tente de novo |
| “Não foi possível conectar” | sem internet, proxy ou firewall | verifique a conexão; o resto do ATENA continua funcionando |
| “limite mensal … seria ultrapassado” | limite interno do ATENA | ajuste em Configurações |

## Fontes

- [Preços da API (OpenAI)](https://developers.openai.com/api/docs/pricing)
- [Modelos (OpenAI)](https://developers.openai.com/api/docs/models)
- [Descontinuações (OpenAI)](https://developers.openai.com/api/docs/deprecations)
- [Quickstart — criação da chave (OpenAI)](https://developers.openai.com/api/docs/quickstart)
- [Limites de gastos (OpenAI)](https://developers.openai.com/api/docs/guides/spend-limits)
- [Seus dados na API (OpenAI)](https://developers.openai.com/api/docs/guides/your-data)
