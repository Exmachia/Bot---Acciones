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
- [ ] Fase 2: Telegram (alertas en español). Siguiente paso: el usuario crea el bot con
      @BotFather, pone TELEGRAM_BOT_TOKEN en .env, le manda "hola" al bot y sacamos el
      chat_id con getUpdates. Alerta: ticker, empresa, catalizador, resumen 2 líneas, cuánto
      se ha movido, entrada/stop/objetivo/fecha límite, confianza, link a la fuente y la nota
      "Verifica que esté disponible en el SIC de tu casa de bolsa". Pre-apertura = lista
      "pendiente de confirmación" (RVOL no medible); alerta cuando se confirma intradía.
- [ ] Fase 3: Claude (claude-haiku-5-5) lee noticias de Alpaca → JSON: sorpresa real o letra
      chica, sentimiento, riesgos, confianza 1-5, si subió guía (suma punto).
- [ ] Fase 4: SQLite, cierre diario de operaciones simuladas (stop/objetivo/fecha límite),
      reporte semanal domingo con costos (config.yaml: comisión GBM 0.25%+IVA conservador,
      0.5% tipo de cambio, 10% ISR sobre ganancia neta) vs. SPY. Pruebas del cálculo.
- [ ] Fase 5: backtest 2 años; explicar look-ahead y supervivencia; decir claramente si no
      le gana a SPY después de costos. Pendiente confirmar historia del plan gratis de Finnhub.
- [ ] Fase 6: GitHub Actions (repo público) con cron; costo mensual.

## Notas técnicas
- Horarios en America/New_York con zoneinfo; mostrar en CDMX.
- Alpaca gratis: precio en vivo IEX; volumen SIP con 15 min de retraso (por eso RVOL "hasta hace 16 min").
- Ventana de reportes: cuenta si la sesión en juego es su primer día de reacción (`is_fresh`).
- Mac del usuario: Python 3.15 en `~/Bot---Acciones/.venv`.
