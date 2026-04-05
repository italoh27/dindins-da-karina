# Monitoramento para não deixar o site dormir

Este projeto agora inclui a rota de saúde `\/healthz`.

Use um serviço externo como UptimeRobot ou Better Stack para acessar:

`https://SEU-APP.onrender.com/healthz`

Sugestão de intervalo: a cada 5 minutos.

A resposta esperada é um JSON com `ok: true`.
