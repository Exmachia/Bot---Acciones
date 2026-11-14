# Bot de alertas PEAD (solo simulación)

Bot que **manda alertas** de acciones de EE. UU. para operaciones de 1 a 5 días con la
estrategia de *deriva post-resultados* (PEAD: la tendencia de una acción a seguir
moviéndose en la dirección de una sorpresa de resultados durante días).

> ⚠️ **No compra ni vende con dinero real.** Solo alerta y registra operaciones
> *simuladas*. Alpaca se usa únicamente en modo paper; el código se detiene si detecta
> el endpoint real o llaves que no sean de paper, y no existe ninguna opción para activarlo.
> Nada de esto es asesoría financiera.

## Estado por fases

| Fase | Estado |
|---|---|
| 1. Datos y filtros | ✅ |
| 2. Telegram | ✅ |
| 3. Claude (noticias) | ✅ |
| 4. Registro y reporte | ✅ |
| 5. Backtest | ✅ (resultado: no le gana a SPY) |
| 6. Despliegue | ✅ (GitHub Actions) |

## Instalación

Requiere Python 3.11 o superior.

```bash
git clone <este repo> && cd Bot---Acciones
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # y llena tus llaves (el .env nunca se sube a git)
```

## Cómo obtener cada llave

### Finnhub (gratis)
1. Entra a https://finnhub.io y da clic en **Get free API key**.
2. Regístrate con tu correo y confírmalo.
3. En el *Dashboard* copia la **API Key** → `FINNHUB_API_KEY` en `.env`.

### Telegram (gratis)
1. En Telegram busca **@BotFather** (palomita azul) y mándale `/newbot`.
2. Dale un nombre (ej. `Alertas de Acciones`) y un usuario que termine en `bot`.
3. Copia el **token** que te da → `TELEGRAM_BOT_TOKEN` en `.env`.
4. Abre tu bot, dale **Iniciar** y mándale `hola`.
5. Saca tu número de chat con
   `curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"` (busca `"chat":{"id":...}`)
   → `TELEGRAM_CHAT_ID` en `.env`.

### Anthropic / Claude (de pago, muy barato)
1. Entra a https://console.anthropic.com y crea tu cuenta.
2. En **Billing** carga crédito (con $5 USD alcanza para meses) y deja **auto-reload apagado**.
3. En **API Keys** → **Create Key** → elige **Somewhere else** (usar una llave) → nombre `bot-acciones`.
4. Copia la llave (`sk-ant-...`, solo se muestra una vez) → `ANTHROPIC_API_KEY` en `.env`.

Sin esta llave el bot funciona igual, pero las alertas salen sin lectura de noticias.

### Alpaca (cuenta paper, gratis)
1. Regístrate en https://alpaca.markets. **No necesitas depositar ni verificar identidad** para paper.
2. En el dashboard, arriba a la izquierda, asegúrate de estar en **Paper Trading**
   (no en *Live*).
3. En la sección **API Keys** da clic en **Generate New Keys**.
4. Copia **Key ID** (empieza con `PK`) → `ALPACA_API_KEY_ID`, y **Secret Key**
   → `ALPACA_API_SECRET_KEY`. El secreto solo se muestra una vez.

## Cómo correrlo

```bash
python -m pytest                       # pruebas
python -m pead_bot.cli scan --dry-run  # escanea e imprime por qué pasa o no cada candidato
python -m pead_bot.cli scan            # igual, y además manda las alertas a Telegram
python -m pead_bot.cli telegram-test   # manda una alerta de EJEMPLO para ver el formato
python -m pead_bot.cli inbox           # lee tus toques de ✅ Voy / ❌ Paso
python -m pead_bot.cli listen          # escucha y contesta tus botones al instante (Ctrl+C para salir)
python -m pead_bot.cli backtest        # prueba la estrategia con 1 año de datos (tarda ~1.5 h la 1a vez)
python -m pead_bot.cli guia            # manda a Telegram la explicación del bot en palabras sencillas
python -m pead_bot.cli track           # cierre diario (16:30 NY) de operaciones simuladas
python -m pead_bot.cli report          # reporte semanal (domingo) con costos y contra SPY
```
`track` y `report` aceptan `--dry-run` para imprimir sin mandar a Telegram.

### Tu regla de monto (`capital` en config.yaml)
Tope por jugada: **$10,000 MXN**. El monto sale de la confianza de Claude:
5 → $10,000 · 4 → $7,500 · 3 → $5,000 · sin lectura de noticias → $5,000
(con menos de 3 no hay alerta). Sin límite total: cada alerta trae su monto aunque haya
otras abiertas, y te dice cuánto llevarías metido en total.

Cada alerta va por bloques separados: **🎯 La jugada** (monto, entrada, stop y objetivo con
lo que ganarías o perderías en pesos, fecha máxima), **📰 Qué pasó** y **🤖 Claude**. Cada operación simulada guarda
su monto y el reporte usa ese mismo monto. Todo es bajo tu propio riesgo.

### Qué manda a Telegram
- ⏳ **Pendientes de confirmación** (antes de abrir): pasan todo menos el volumen
  relativo, que aún no se puede medir. No es señal todavía.
- 🟢 **Alerta**: ya pasó todos los filtros. Trae catalizador, resumen, cuánto se ha
  movido, plan (entrada, stop, objetivo, fecha límite), puntos y link al reporte en la SEC.
- Cada acción se avisa una sola vez por día (registro en `data/cache/sent.json`).

### Lectura de noticias con Claude (fase 3)
Antes de avisar, Claude (`claude-haiku-5-5`) lee las noticias de Alpaca de esa acción y devuelve:
si la sorpresa viene del negocio o de algo de una sola vez, la letra chica, si la empresa
subió su guía (suma 1 punto), sentimiento, hasta 3 riesgos y una **confianza de 1 a 5**.
- Si la confianza es menor a `llm.min_confidence` (3), no se manda la alerta.
- Solo se analizan las acciones que se van a avisar, y cada lectura se guarda por día
  (`data/cache/analysis/`) para no pagarla dos veces. Costo aproximado: $0.001 USD por lectura.
- Las noticias se le pasan a Claude como datos; el prompt le prohíbe seguir instrucciones que vengan en ellas.

## La estrategia, paso a paso

Todos los números están en `config.yaml`.

1. **Candidatos:** reportaron desde la última sesión (un reporte del viernes en la tarde
   sigue vigente el lunes) y su **EPS** (utilidad por acción) real
   superó al estimado de analistas por más de 10%. +1 punto si también superó **ingresos**
   (ventas totales), +1 si subió su **guía** (lo que la empresa espera ganar a futuro).
2. **Calidad:** precio > $5, **volumen promedio** > 1 M acciones/día (que sea fácil comprar
   y vender), **valor de mercado** > $1,000 M USD (empresa no diminuta).
3. **No perseguir:** si ya subió más de 8% desde el cierre previo al reporte, se descarta.
4. **Confirmación:** **volumen relativo** > 2x (hoy se negocia el doble de lo normal para esta hora).
5. Plan: entrada al precio actual, **stop-loss** −5% (salida para limitar pérdida),
   objetivo +8%, salida forzada a los 5 días hábiles.

### Operaciones simuladas y reporte (fase 4)
Cada alerta 🟢 que sale se guarda en `data/pead.sqlite` como si hubieras comprado al precio
de la alerta (sin dinero real). El cierre diario la cierra sola:
- **Día de entrada:** solo cuenta lo que pasó *después* de la alerta.
- **Salto a la baja o al alza** (abre más allá del stop u objetivo): sale al precio de apertura.
- **Toca stop y objetivo el mismo día:** se asume que tocó primero el stop (lo conservador).
- **Fecha límite:** sale al cierre de ese día.

**Costos** (`config.yaml`): comisión 0.25% + IVA al comprar y al vender, 0.5% de tipo de
cambio, e ISR de 10% sobre la ganancia neta del periodo (las pérdidas restan antes).
Con eso, cada operación necesita subir ~1.1% solo para no perder.

**Contra SPY:** para cada operación se mide qué hizo SPY (el S&P 500) en esos mismos días,
sin costos. Si la estrategia no le gana, no vale la pena. El reporte avisa que hacen falta
al menos 20 operaciones antes de sacar conclusiones.

### Botones ✅ Voy / ❌ Paso
Cada alerta 🟢 trae botones. Si tocas **Voy**, el bot anota la jugada y te escribe una sola
vez cuando toque vender: 🛑 tocó el stop, ✅ llegó al objetivo, ⏱️ hoy es el último día, o
🔔 terminó con el cierre. El bot no compra ni vende; solo te avisa. Solo acepta toques de tu chat.

### Backtest (fase 5): resultado
1 año (nov 2025 a nov 2026), datos gratis, sin el filtro de Claude, $5,000 MXN por operación:

| | |
|---|---|
| Operaciones | 1,032 (≈ 4 por día de bolsa) · ganadoras 41% |
| Promedio por operación | +0.17% bruto · **−0.91% después de costos** |
| SPY en los mismos días | +0.21% por operación |
| Total | **−46,780 MXN** (SPY en esos días: +10,595) |

**Veredicto: no le gana a SPY**, ni siquiera antes de costos. Rango probable: −1.47% a −0.75%
por operación contra SPY. Cómo se evitaron las trampas: hora exacta del reporte (SEC), revisión
cada 15 min solo con datos ya publicados, volumen con 16 min de retraso, salidas conservadoras.
Limitaciones: solo 1 año, solo empresas que existen hoy (eso hace que se vea *mejor*, no peor),
valor de mercado aproximado, sin el filtro de Claude (no se puede probar sin ver el futuro).

### Que corra solo (fase 6, GitHub Actions)
`.github/workflows/bot.yml` corre gratis (repo público) en estos horarios (CDMX):
- **Lun-vie cada 15 min, 6:00 a 15:45:** escanea, manda alertas, revisa tus jugadas y se queda
  escuchando tus botones hasta la siguiente corrida.
- **Lun-vie 15:45:** cierre diario de operaciones simuladas.
- **Domingo 17:00:** reporte semanal.

**El historial** (operaciones, decisiones, avisos ya mandados) se guarda **cifrado** (AES-256)
en la rama `estado`, con la clave `STATE_KEY`. Sin esa clave nadie puede leerlo.

**Llaves en GitHub** (una sola vez): en el repo → **Settings → Secrets and variables →
Actions → New repository secret**, crea uno por cada línea de tu `.env`:
`FINNHUB_API_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`, `STATE_KEY`. Luego en **Actions → Bot de alertas →
Run workflow** puedes correrlo a mano para probar.

Una vez que corre en GitHub, no lo corras también en tu compu (tendrías dos historiales).

### Detalles técnicos que importan
- **Horarios:** se calculan en hora de Nueva York (que cambia por horario de verano) y se
  muestran en hora de CDMX (que ya no cambia). La diferencia es de 1 h en invierno y 2 h en verano.
- **Datos gratuitos de Alpaca:** el precio en vivo viene de la bolsa IEX (tiempo real, pero
  solo una parte del volumen). El volumen se toma del feed consolidado SIP, que en el plan
  gratuito solo se puede consultar con 15 min de retraso. El volumen relativo, entonces,
  es "hasta hace 16 minutos".
- **Hora del reporte desconocida:** se asume que fue después del cierre (lo conservador).
