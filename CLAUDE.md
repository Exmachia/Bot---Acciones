# Contexto para Claude

Bot de ALERTAS de acciones de EE. UU. (estrategia PEAD: deriva post-resultados filtrada
con noticias) para operaciones de 1 a 5 días. El usuario vive en México
(America/Mexico_City), es principiante en finanzas pero sabe de tecnología.

## Reglas que no se rompen
- El bot NO compra ni vende con dinero real. Solo alertas y operaciones SIMULADAS (SQLite).
- Alpaca solo paper: URL fija en `src/pead_bot/safety.py`, sin opción para cambiarla;
  el código se niega a correr si detecta el endpoint real o llaves que no empiecen con "PK".
  No implementar ninguna función que mande órdenes.
- Llaves solo en `.env` (está en .gitignore). Nunca pedir que las pegue en el chat.

## Cómo trabajar con el usuario
- Español. Instrucciones cortas, paso por paso, sin textos largos.
- Explicar términos financieros la primera vez que aparezcan, en una línea.
- Avanzar por fases; cada una debe funcionar sola. Pruebas para filtros y cálculos.
- Pedir cada llave cuando se necesite y decir exactamente cómo sacarla.
- Rama de trabajo: `claude/post-earnings-drift-bot-s5b6w4`.

## Estado
- [x] Fase 1: datos (Finnhub calendario/perfil, Alpaca paper calendario/barras/precio/noticias),
      filtros, plan, candados de seguridad. Probado con datos reales: `scan --dry-run` funciona.
- [x] Fase 2: Telegram. `notify/format.py` (textos HTML, puros), `notify/telegram.py`
      (el token nunca sale en errores), `notify/dispatch.py` (pendientes pre-apertura y
      alertas, sin repetir: `data/cache/sent.json`). `scan` manda, `scan --dry-run` solo
      imprime, `telegram-test` manda un ejemplo. Bot: @alertas_acciones_mx_bot, probado.
      "Confianza" por ahora = puntos (de 3); en fase 3 se suma la confianza 1-5 de Claude.
- [x] Fase 3: Claude (claude-haiku-5-5, SDK anthropic 2.8, `messages.parse` con Pydantic).
      `llm/news.py` (prompt + NewsAnalysis: resumen, sorpresa_real, letra_chica, guia,
      sentimiento, riesgos, confianza 1-5), `llm/enrich.py` (solo analiza lo que se va a
      avisar; caché por día en data/cache/analysis/; guía "subio" suma punto). Confianza
      < llm.min_confidence no se manda. Sin ANTHROPIC_API_KEY el bot sigue sin lectura.
      Probado con noticias reales (KSPI). El usuario cargó $20 de crédito.
- [x] Fase 4: `store/db.py` (SQLite data/pead.sqlite, una fila por alerta mandada, con SPY
      de entrada), `tracking/costs.py` (comisión+IVA ida y vuelta, FX, ISR sobre neto del
      periodo, punto de equilibrio ~1.1%), `tracking/exits.py` (gap → apertura; stop y
      objetivo el mismo día → stop; fecha límite → cierre), `tracking/daily.py` (día de
      entrada solo minutos tras la alerta; solo sesiones terminadas + 16 min),
      `tracking/report.py` (cierre y semanal vs. SPY mismos días sin costos; aviso < 20 ops).
      Comandos `track` y `report` (con --dry-run).
- [x] Extra: regla de monto elegida por el usuario (`capital`): tope 10,000 MXN por jugada,
      % según confianza de Claude (5→100, 4→75, 3→50; sin lectura→50), sin límite total.
      Alerta compacta por bloques con separadores (━━━): 🎯 LA JUGADA (monto, entrada,
      stop/objetivo con MXN, fecha máxima, abiertas), 📰 QUÉ PASÓ, 🤖 CLAUDE; cierra con
      "Bajo tu propio riesgo". El usuario pidió no usar el título "En palabras sencillas".
      Cada trade guarda `monto_mxn`; el reporte lo usa. Comando `guia` manda la explicación.
- Pregunta pendiente del usuario: compra/venta automática. GBM no tiene API pública (no
  automatizar con su usuario/contraseña). Si se quiere: después de la fase 5, ensayo con
  órdenes paper en Alpaca; luego broker con API (IBKR/Alpaca live). Requiere que el usuario
  cambie la regla "no compra ni vende" de arriba.
- [x] Fase 5: `backtest/` (data.py: universo Alpaca, Finnhub stock/earnings 4 trimestres,
      hora 8-K item 2.02 de SEC con UA genérico sin datos personales, acciones en circulación;
      sim.py: día de reacción, revisión cada 15 min desde 10:00 NY con precio previo y volumen
      −16 min, salidas de fase 4; run.py: reporte y veredicto con rango ±2 EE). Caché en
      data/cache/backtest (1a corrida ~1.5 h). RESULTADO nov-2025→nov-2026: 1,032 operaciones,
      41% ganadoras, +0.17% bruto, −0.91% neto, SPY mismos días +0.21% → NO le gana a SPY
      (−1.11%/op, rango −1.47 a −0.75). Sin bolsillo claro de ventaja; no sobreajustar reglas
      al mismo año. Finnhub gratis: calendario solo ~1 mes atrás.
- [x] Botones ✅ Voy / ❌ Paso (`notify/decisions.py`): getUpdates en cada corrida (solo del
      chat del usuario), columnas `decision` y `notices`; avisos de venta una vez: stop,
      objetivo, último día, cierre. Comando `inbox`. Alerta de ejemplo usa símbolo EJEM (no
      se guarda).
- [x] Fase 6: `.github/workflows/bot.yml` (cron UTC: */15 12-21 lun-vie → scan + listen 11 min;
      21:45 lun-vie → track; dom 23:00 → report; workflow_dispatch con comando). Historial en
      rama `estado` cifrado con AES-256 (`scripts/state.sh`, secreto STATE_KEY generado en .env).
      `concurrency: bot` para no pisar el historial. Repo público y rama de trabajo = default
      (los cron solo corren en la rama default). Secrets los pone el usuario en GitHub. Sin secretos, la corrida no hace nada (no falla).

## Notas técnicas
- Horarios en America/New_York con zoneinfo; mostrar en CDMX.
- Alpaca gratis: precio en vivo IEX; volumen SIP con 15 min de retraso (por eso RVOL "hasta hace 16 min").
- Ventana de reportes: cuenta si la sesión en juego es su primer día de reacción (`is_fresh`).
- Mac del usuario: Python 3.15 en `~/Proyectos/Bot---Acciones/.venv`.
