
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


## Super Update

Esta versão adiciona:

- Central de Entradas com histórico de novos membros.
- Detecção de convite utilizado por comparação do contador de usos.
- Ranking de quem mais trouxe membros.
- Detecção separada de Vanity URL/link personalizado, quando disponível.
- Novos níveis de acesso para Departamento de Banimentos, Departamento de Entrada e Responsável pela Patente de Minecraft.
- Logo oficial aplicada ao login, cabeçalho e prévia do Discord.
- Central Administrativa e Mensagens do Bot mantidas no painel.

### Permissões necessárias no Discord

Para o rastreamento funcionar corretamente:

1. Ative **Server Members Intent** no Discord Developer Portal.
2. O bot precisa conseguir visualizar os convites do servidor (permissão **Manage Guild / Gerenciar Servidor** é necessária para buscar a lista de convites pela API).

### Limitação importante: Server Tags

A API do Discord não fornece ao evento de entrada uma informação confiável dizendo que o membro entrou "através de uma Server Tag".
Esta versão não inventa esse dado. Ela identifica convites normais e Vanity URL quando o contador permite; quando nenhuma origem pode ser comprovada, registra como `desconhecida`.


## Central de Atualizações

A aba **Atualizações** é exclusiva para acesso total e controla o ciclo:

1. Escolha o canal oficial de atualizações.
2. Cole uma mensagem de **Futuras atualizações** e publique.
3. Se a prévia mudar, publique novamente: somente a prévia futura anterior é substituída.
4. Quando a versão sair, cole as **Notas da atualização**.
5. O painel publica as notas como mensagens normais do Discord e só depois remove a prévia futura.
6. Notas de versões já lançadas nunca são apagadas pelo painel.
7. O histórico local começa a ser registrado a partir desta versão no arquivo `/data/atualizacoes_painel.json`.

Mensagens longas são divididas automaticamente em partes de até aproximadamente 1900 caracteres.
