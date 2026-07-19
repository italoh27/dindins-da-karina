# Migração para Neon + Render + Mercado Pago

## O que já foi preparado
- app.py refeito para usar PostgreSQL no Neon
- migração automática de `sabores.json`, `pedidos.json` e `config_loja.json` para o banco na primeira execução com `DATABASE_URL`
- checkout por redirecionamento do Mercado Pago
- webhook `/webhooks/mercado-pago`
- cancelamento de pedido com devolução de estoque quando ainda não estiver pago
- exportação Excel mantida

## Variáveis de ambiente no Render
Copie do arquivo `.env.example`:
- `DATABASE_URL`
- `SECRET_KEY`
- `ADMIN_PASSWORD`
- `PUBLIC_BASE_URL`
- `MERCADO_PAGO_ACCESS_TOKEN`
- `NUMERO_ITALO`
- `NUMERO_KARINA`
- `CHAVE_PIX`
- `NOME_PIX`
- `BANCO_PIX`

## Build e start no Render
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`

## Observação importante do Mercado Pago
O checkout por preferência do Checkout Pro redireciona o cliente para o ambiente do Mercado Pago. Esse fluxo pode oferecer Pix e cartão, conforme os meios habilitados na sua conta do Mercado Pago e a configuração da sua integração. O retorno automático e as notificações por webhook são recursos documentados pelo Mercado Pago. citeturn721694search5turn731700search1turn960727search0

## Primeira subida
1. Crie o banco no Neon e copie a `DATABASE_URL`.
2. Suba este projeto no GitHub.
3. Crie o Web Service no Render.
4. Adicione as variáveis de ambiente.
5. Faça o deploy.
6. Entre no admin e teste um pedido.
7. No primeiro boot com banco configurado, o app migra os dados do JSON para o Postgres.
