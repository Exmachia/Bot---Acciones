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
| 3. Claude (noticias) | pendiente |
| 4. Registro y reporte | pendiente |
| 5. Backtest | pendiente |
| 6. Despliegue | pendiente |

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
```

### Qué manda a Telegram
- ⏳ **Pendientes de confirmación** (antes de abrir): pasan todo menos el volumen
  relativo, que aún no se puede medir. No es señal todavía.
- 🟢 **Alerta**: ya pasó todos los filtros. Trae catalizador, resumen, cuánto se ha
  movido, plan (entrada, stop, objetivo, fecha límite), puntos y link al reporte en la SEC.
- Cada acción se avisa una sola vez por día (registro en `data/cache/sent.json`).

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

### Detalles técnicos que importan
- **Horarios:** se calculan en hora de Nueva York (que cambia por horario de verano) y se
  muestran en hora de CDMX (que ya no cambia). La diferencia es de 1 h en invierno y 2 h en verano.
- **Datos gratuitos de Alpaca:** el precio en vivo viene de la bolsa IEX (tiempo real, pero
  solo una parte del volumen). El volumen se toma del feed consolidado SIP, que en el plan
  gratuito solo se puede consultar con 15 min de retraso. El volumen relativo, entonces,
  es "hasta hace 16 minutos".
- **Hora del reporte desconocida:** se asume que fue después del cierre (lo conservador).
