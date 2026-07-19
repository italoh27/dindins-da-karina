# O que foi otimizado

- Pool de conexões com Postgres usando `psycopg_pool` para reduzir custo de abrir conexão a cada request.
- `openpyxl` agora é carregado só na rota de exportação, melhorando o tempo de boot.
- Start command do Gunicorn ajustado para Render com 1 worker e 4 threads.
- Uso de `/dev/shm` para arquivos temporários do Gunicorn.

## Importante

Se o serviço estiver no plano Free da Render, ele ainda vai dormir após 15 minutos sem tráfego e pode levar até cerca de 1 minuto para voltar. Para eliminar isso de verdade, mude o serviço para um plano pago.
