# Câmbio Web

Backend separado para ler a ExchangeRate-API, manter cache local e servir os dados para a página web.

## Como usar

1. Defina a URL da API no ambiente:

   `CAMBIO_API_KEY=SEU-API-KEY`

   Opcionalmente, defina:

   `CAMBIO_BASE_CURRENCY=USD`

   Se preferir autenticação por header:

   `CAMBIO_API_AUTH_MODE=bearer`

   e use `CAMBIO_API_KEY` como o token.

2. Rode o backend:

   `python backend.py`

3. Abra o arquivo `index.html` no navegador.

## Como ver na web

Se este diretório for publicado como repositório no GitHub Pages, a página principal fica no `index.html` da raiz.

O fluxo é este:

1. O GitHub Actions atualiza `cambio_cache.json` de segunda a sexta às 11:30.
2. O `index.html` da raiz carrega a tabela diretamente.
3. A página lê o cache publicado e mostra os valores sem depender da sua máquina ligada.

Se quiser ver localmente, abra `index.html` ou rode o backend e acesse `http://127.0.0.1:8000/api/cambio`.

## Como publicar no GitHub Pages

1. Faça commit e push de todos os arquivos.
2. No GitHub, abra o repositório.
3. Vá em `Settings` > `Pages`.
4. Em `Build and deployment`, selecione `Deploy from a branch`.
5. Escolha a branch principal, normalmente `main`.
6. Em pasta, selecione `/ (root)`.
7. Salve.

Depois disso, o link do site fica disponível na própria tela de `Pages`.

Se a publicação usar o conteúdo desta pasta como raiz do repositório, mantenha estes arquivos no próprio diretório:

- `index.html`.
- `cambio_cache.json`.
- `spreads.json`.
- `backend.py`.
- `update_cache.py`.
- `.github/workflows/update-cambio.yml`.
- `.nojekyll`.

## Como atualizar os spreads

Se quiser mudar o spread sem mexer no backend:

1. Abra `spreads.json`.
2. Ajuste os percentuais por moeda.
3. Faça commit e push.
4. Aguarde o próximo ciclo do GitHub Actions.

Os valores são decimais. Exemplo: `0.02` = 2%.

## Endpoints

- `GET /api/cambio` retorna o cache atual.
- `POST /api/refresh` força uma atualização manual.
- `GET /api/health` retorna status simples.

## Agendamento

O backend tenta atualizar automaticamente todos os dias às 11:30. Para isso funcionar, o processo precisa ficar em execução contínua. Se preferir, rode esse script pelo Agendador de Tarefas do Windows no login do usuário ou às 11:29.

Importante: a API paga só é consultada uma vez por dia. O backend grava a tentativa do dia no cache local e não faz nova chamada externa até virar a data.

## GitHub Actions

Para atualizar mesmo com a máquina desligada, existe um workflow em `.github/workflows/update-cambio.yml`.

Ele roda de segunda a sexta às 11:30 no horário de Brasília, o que equivale a `14:30 UTC`.

Crie estes segredos no repositório:

- `CAMBIO_API_KEY` com a chave da ExchangeRate-API.
- `CAMBIO_API_AUTH_MODE` se você quiser controlar o modo de autenticação no GitHub Actions; use `url` ou `bearer`.
- `CAMBIO_BASE_CURRENCY` se quiser trocar a base, por exemplo `USD` ou `BRL`.

Se o seu plano usar autenticação via URL, o backend chama:

`https://v6.exchangerate-api.com/v6/SUA-CHAVE/latest/USD`

Se usar Bearer, o backend chama a URL sem a chave e envia o header:

`Authorization: Bearer SUA-CHAVE`

## Spread

O spread por moeda fica em `spreads.json`.

Exemplo:

`{"USD": 0.02, "EUR": 0.03}`

Como atualizar:

1. Abra `spreads.json`.
2. Altere o percentual de cada moeda.
3. Faça commit e push para o GitHub.
4. Na próxima execução do workflow, o novo spread entra no cálculo.

Os valores representam fração decimal. Por exemplo, `0.02` significa 2% e `0.05` significa 5%.

## Formato esperado da API

O backend lê o formato padrão da ExchangeRate-API e também tenta aceitar formatos simples caso você troque a fonte no futuro.

- resposta padrão com `conversion_rates`
