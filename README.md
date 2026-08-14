
# Painel web do `/menu` — Railway

Este projeto coloca o painel web e um bot Discord de teste no mesmo serviço Railway.

## Variáveis obrigatórias no Railway

- `TOKEN`: token do bot Discord.
- `PANEL_PASSWORD`: senha usada para entrar no painel.
- `PANEL_SECRET_KEY`: texto aleatório grande usado para proteger a sessão do site.
- `DATA_DIR`: use `/data`.
- `GUILD_ID`: ID do seu servidor Discord (opcional, mas útil para sincronizar o comando rapidamente no servidor de teste).

## Volume

Para as alterações do painel continuarem salvas mesmo após reinícios/deploys:

1. Adicione um Volume ao serviço.
2. Monte o Volume em `/data`.
3. Deixe `DATA_DIR=/data`.

## Domínio

No serviço Railway:

Settings → Networking → Public Networking → Generate Domain

O link gerado abrirá a tela de login do painel.

## Como usar

1. Abra o link público do Railway.
2. Digite o valor definido em `PANEL_PASSWORD`.
3. Edite título, descrição, cor, botões e respostas.
4. Clique em "Salvar alterações".
5. Use `/menu` no Discord.

## Importante

Este pacote é uma versão isolada para testar o painel. Para colocar no bot principal sem perder as funções existentes, a lógica do `main.py` precisa ser mesclada ao arquivo atual do bot.
