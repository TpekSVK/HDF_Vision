# app/services/stats_service.py
from __future__ import annotations

from typing import Dict, Any, Optional
from app.services.db_service import DbService


class StatsService:
    """
    Jednoduchá vrstva nad DbService pre agregované štatistiky.
    Vždy vracia slovník v tvare:
        {
            "total": int,
            "ok": int,
            "nok": int,
            "yield": float,   # 0.0..1.0
            "total_cycle_time_ms": float,
        }
    """

    def __init__(self, db: Optional[DbService] = None):
        self.db = db or DbService()

    # ---------- Public API ----------

    def daily_for_recipe(self, recipe: str, *, view_id: str | None = None) -> Dict[str, Any]:
        """
        Vráti denné štatistiky pre recept.
        Ak recept neexistuje alebo DB zlyhá, vráti default prázdne štatistiky.
        """
        try:
            rid = self.db.recipe_id(recipe)
        except Exception as e:
            # Tichý fallback – aplikácia musí bežať ďalej
            # (prípadne sem môže ísť logger.warning)
            return self._default_summary()

        if rid is None:
            return self._default_summary()

        try:
            raw = self.db.daily_stats(rid, view_id=view_id)
        except Exception:
            return self._default_summary()

        return self._normalize_summary(raw)

    # ---------- Interné pomocné metódy ----------

    @staticmethod
    def _default_summary() -> Dict[str, Any]:
        return {"total": 0, "ok": 0, "nok": 0, "yield": 0.0, "total_cycle_time_ms": 0.0}

    @staticmethod
    def _normalize_summary(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Zaistí prítomnosť a správne typy kľúčov total/ok/nok/yield.
        Ak DB `yield` nedodá alebo je nekonzistentný, spočíta sa z ok/total.
        """
        if not isinstance(raw, dict):
            return StatsService._default_summary()

        total = _as_int(raw.get("total", raw.get("total_count", 0)))
        ok = _as_int(raw.get("ok", raw.get("pass", raw.get("ok_count", 0))))
        nok = _as_int(raw.get("nok", raw.get("fail", raw.get("nok_count", 0))))
        total_cycle_time_ms = _as_float(
            raw.get("total_cycle_time_ms", raw.get("total_test_duration_ms", 0.0))
        )

        # Ak DB nepočítala total, spočítame ho z ok+nok (ale neprepíšeme nenulové total z DB)
        if total <= 0:
            inferred = ok + nok
            total = inferred if inferred > 0 else 0

        # yield z DB, ak je validný (0..1); inak dopočítať
        y = raw.get("yield")
        if not _is_valid_yield(y):
            y = _calc_yield(ok, total)

        # normalizácia ok/nok na interval [0, total]
        ok = max(0, min(ok, total))
        nok = max(0, min(nok, total - ok))

        return {
            "total": int(total),
            "ok": int(ok),
            "nok": int(nok),
            "yield": round(float(y), 4),
            "total_cycle_time_ms": max(0.0, float(total_cycle_time_ms)),
        }


# ---------- Drobná pomocná utilita (lokálne) ----------

def _as_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _is_valid_yield(y: Any) -> bool:
    try:
        yf = float(y)
        return 0.0 <= yf <= 1.0
    except Exception:
        return False


def _calc_yield(ok: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return float(ok) / float(total)
