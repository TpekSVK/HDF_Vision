import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.db_service import DbService


def _init_db(tmp_path: Path) -> DbService:
    db_path = tmp_path / "test.db"
    if db_path.exists():
        db_path.unlink()
    return DbService(db_path=db_path)


def test_export_csv_today_includes_rows_based_on_ts_ms(tmp_path: Path):
    db = _init_db(tmp_path)
    rid = db.ensure_recipe("default")

    now_ms = int(time.time() * 1000)
    db.insert_result(
        ts_ms=now_ms,
        recipe_id=rid,
        ok=True,
        metrics={},
        thumb_path="thumb.png",
        full_path="full.png",
        meta_json="{}",
    )

    # Simuluj prípad, keď created_at ostal včera (UTC), ale ts_ms je dnešný
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    db.conn().execute(
        "UPDATE results SET created_at=? WHERE recipe_id=?",
        (yesterday.strftime("%Y-%m-%d %H:%M:%S"), rid),
    )
    db.conn().commit()

    out_path = tmp_path / "export.csv"
    db.export_csv_today(rid, str(out_path))

    with out_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    assert len(rows) == 2  # header + jedna dnešná položka cez ts_ms


def test_daily_stats_counts_use_ts_ms(tmp_path: Path):
    db = _init_db(tmp_path)
    rid = db.ensure_recipe("default")

    now_ms = int(time.time() * 1000)
    db.insert_result(
        ts_ms=now_ms,
        recipe_id=rid,
        ok=True,
        metrics={"total_cycle_time_ms": 42.5},
        thumb_path="thumb.png",
        full_path=None,
        meta_json="{\"total_cycle_time_ms\": 42.5}",
    )

    # Posuň created_at na včerajší deň, aby simuloval rozdiel medzi UTC a lokálnym časom
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    db.conn().execute(
        "UPDATE results SET created_at=? WHERE recipe_id=?",
        (yesterday.strftime("%Y-%m-%d %H:%M:%S"), rid),
    )
    db.conn().commit()

    stats = db.daily_stats(rid)

    assert stats["total"] == 1
    assert stats["ok"] == 1
    assert pytest.approx(stats["total_cycle_time_ms"], rel=1e-6) == 42.5
