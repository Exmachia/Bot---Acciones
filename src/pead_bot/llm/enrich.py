"""Agrega la lectura de noticias (fase 3) a los candidatos que se van a avisar.

- Solo analiza los que pasan todo, o los pendientes antes de abrir: así se gasta
  poco en Claude.
- Guarda cada lectura por día y acción en data/cache/analysis/ para no repetir
  la llamada en cada escaneo de 15 minutos.
- Si la empresa subió su guía, se suma el punto que define config.yaml.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path

from ..config import Config
from ..notify.dispatch import is_pre_open
from ..scanner import ScanResult
from ..schedule import NY
from ..strategy.filters import evaluate
from .news import NewsAnalysis

log = logging.getLogger(__name__)


def needs_analysis(ev, pre_open: bool) -> bool:
    return ev.passed or (pre_open and ev.pending_only)


def enrich(res: ScanResult, cfg: Config, ap, analyzer, cache_dir: Path) -> int:
    """Devuelve cuántas llamadas nuevas a Claude se hicieron."""
    if res.active_day is None:
        return 0
    pre = is_pre_open(res)
    calls = 0
    for i, ev in enumerate(res.evaluations):
        if not needs_analysis(ev, pre):
            continue
        d = ev.data
        tag = getattr(analyzer, "cache_tag", None)
        cls = getattr(analyzer, "output_cls", NewsAnalysis)
        f = Path(cache_dir) / f"{res.active_day.isoformat()}_{d.symbol}{'_' + tag if tag else ''}.json"
        a, src = None, None
        if f.exists():
            try:
                cached = json.loads(f.read_text(encoding="utf-8"))
                a = cls.model_validate(cached["analysis"])
                src = tuple(cached["source"]) if cached.get("source") else None
            except (ValueError, KeyError, TypeError):
                a = None
        if a is None:
            start = datetime.combine(d.report_date - timedelta(days=1), time(0), tzinfo=NY)
            try:
                news = ap.news(d.symbol, start, res.now, limit=cfg.llm.max_news_items)
            except Exception as e:  # noqa: BLE001
                log.warning("noticias de %s fallaron: %s", d.symbol, e)
                news = []
            a = analyzer.analyze(d, news)
            calls += 1
            src = (news[0].source or "Noticia", news[0].url) if news and news[0].url else None
            if a is not None:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps({"analysis": a.model_dump(), "source": src},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        if a is None:
            continue
        if a.guia == "subio":
            d.guidance_raised = True
        new = evaluate(d, cfg.strategy, cfg.filters)  # recalcula puntos con la guía
        new.analysis, new.source = a, src
        res.evaluations[i] = new
    return calls
