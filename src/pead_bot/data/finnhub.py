"""Cliente mínimo de Finnhub: calendario de resultados y perfil de empresa."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .http import get_json

BASE = "https://finnhub.io/api/v1"


@dataclass
class EarningsReport:
    symbol: str
    date: date
    hour: str | None          # bmo | amc | dmh | None
    eps_actual: float | None
    eps_estimate: float | None
    revenue_actual: float | None
    revenue_estimate: float | None
    quarter: int | None = None
    year: int | None = None


@dataclass
class CompanyProfile:
    symbol: str
    name: str
    market_cap_usd: float | None
    exchange: str | None
    weburl: str | None = None
    shares_outstanding: float | None = None  # acciones en circulación (unidades)


class FinnhubClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, **params):
        # La llave va en header (no en la URL) para que no aparezca en logs.
        return get_json(f"{BASE}{path}", params=params, headers={"X-Finnhub-Token": self.api_key})

    def earnings_calendar(self, start: date, end: date) -> list[EarningsReport]:
        data = self._get("/calendar/earnings", **{"from": start.isoformat(), "to": end.isoformat()})
        out = []
        for row in data.get("earningsCalendar") or []:
            try:
                d = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            out.append(EarningsReport(
                symbol=row.get("symbol", "").upper(),
                date=d,
                hour=row.get("hour") or None,
                eps_actual=row.get("epsActual"),
                eps_estimate=row.get("epsEstimate"),
                revenue_actual=row.get("revenueActual"),
                revenue_estimate=row.get("revenueEstimate"),
                quarter=row.get("quarter"),
                year=row.get("year"),
            ))
        return out

    def profile(self, symbol: str) -> CompanyProfile | None:
        data = self._get("/stock/profile2", symbol=symbol)
        if not data:
            return None
        mc = data.get("marketCapitalization")  # Finnhub la da en MILLONES de USD
        return CompanyProfile(
            symbol=symbol,
            name=data.get("name") or symbol,
            market_cap_usd=float(mc) * 1_000_000 if mc else None,
            exchange=data.get("exchange"),
            weburl=data.get("weburl"),
            shares_outstanding=(float(data["shareOutstanding"]) * 1_000_000
                                if data.get("shareOutstanding") else None),
        )

    def earnings_history(self, symbol: str) -> list[dict]:
        """Últimos trimestres (plan gratis: 4) con EPS real vs. estimado. Sin fecha de reporte."""
        return self._get("/stock/earnings", symbol=symbol) or []
