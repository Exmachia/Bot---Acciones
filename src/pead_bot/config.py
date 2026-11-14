"""Carga config.yaml (números ajustables) y .env (llaves)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .safety import assert_environment_is_safe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.yaml"


class StrategyCfg(BaseModel):
    lookback_hours: float = 24
    eps_surprise_min_pct: float = 10
    min_abs_eps_estimate: float = 0.01
    points_revenue_beat: int = 1
    points_guidance_raise: int = 1


class FiltersCfg(BaseModel):
    min_price: float = 5
    min_avg_volume: float = 1_000_000
    min_market_cap: float = 1_000_000_000
    max_gain_since_prev_close_pct: float = 8
    min_relative_volume: float = 2.0
    avg_volume_days: int = 20
    min_session_minutes_for_rvol: int = 15


class PlanCfg(BaseModel):
    stop_loss_pct: float = 5
    take_profit_pct: float = 8
    max_holding_days: int = 5


class LlmCfg(BaseModel):
    model: str = "claude-haiku-5-5"
    max_news_items: int = 10
    min_confidence: int = 3


class CostsCfg(BaseModel):
    commission_pct: float = 0.25
    vat_on_commission_pct: float = 16
    fx_spread_pct: float = 0.5
    isr_pct: float = 10


class ScheduleCfg(BaseModel):
    premarket_scan_ny: str = "08:30"
    scan_interval_minutes: int = 15
    daily_tracking_ny: str = "16:30"
    weekly_report_ny: str = "18:00"


class Config(BaseModel):
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    filters: FiltersCfg = Field(default_factory=FiltersCfg)
    plan: PlanCfg = Field(default_factory=PlanCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    costs: CostsCfg = Field(default_factory=CostsCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)


def load_config(path: str | Path = DEFAULT_CONFIG) -> Config:
    load_dotenv(ROOT / ".env")
    assert_environment_is_safe(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config.model_validate(raw)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Falta {name} en .env (ver .env.example y README).")
    return value
