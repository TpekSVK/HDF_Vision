from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow the test to be executed directly via ``python tests/test_retention_service.py``
# by ensuring the repository root is on the import path before importing the app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.retention_service import RetentionService


def _create_file(path: Path, size: int, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, times=(mtime, mtime))


def test_delete_old_days_removes_only_older_runs(tmp_path):
    service = RetentionService(base=tmp_path)

    runs_dir = tmp_path / "runs"
    today = datetime.now()
    recent_name = today.strftime("%Y%m%d")
    old_name = (today - timedelta(days=10)).strftime("%Y%m%d")

    recent_dir = runs_dir / recent_name / "recipe" / "thumbs"
    recent_dir.mkdir(parents=True, exist_ok=True)
    old_dir = runs_dir / old_name / "recipe" / "thumbs"
    old_dir.mkdir(parents=True, exist_ok=True)

    # unrelated folder with unexpected naming must stay untouched
    (runs_dir / "not-a-date").mkdir(parents=True, exist_ok=True)

    service._delete_old_days(days=7, verbose=False)

    assert not (runs_dir / old_name).exists(), "directories older than retention window should be removed"
    assert (runs_dir / recent_name).exists(), "directories within retention window must remain"
    assert (runs_dir / "not-a-date").exists(), "non date folders should be ignored"


def test_enforce_size_removes_oldest_files_first(tmp_path):
    service = RetentionService(base=tmp_path)

    runs_dir = tmp_path / "runs" / datetime.now().strftime("%Y%m%d") / "recipe"
    thumbs = runs_dir / "thumbs"
    full = runs_dir / "full"
    overlay = runs_dir / "overlay"

    base_time = time.time() - 100
    first = thumbs / "a.jpg"
    second = full / "b.webp"
    third = overlay / "c.png"

    _create_file(first, size=2048, mtime=base_time - 20)
    _create_file(second, size=2048, mtime=base_time - 10)
    _create_file(third, size=2048, mtime=base_time)

    recipes_dir = tmp_path / "recipes" / "demo"
    _create_file(recipes_dir / "golden.png", size=128, mtime=base_time)

    # Allow at most roughly 3.5 KB worth of files (~3.5e-6 GB).
    service._enforce_size(max_gb=3.5e-6, verbose=False)

    assert not first.exists()
    assert not second.exists()
    assert third.exists(), "newest file should remain until size target is met"
    assert (recipes_dir / "golden.png").exists(), "recipes directory must be untouched"

    total_size = 0
    for root, _dirs, files in os.walk(tmp_path / "runs"):
        for name in files:
            total_size += (Path(root) / name).stat().st_size

    assert total_size <= 2048, "retention should reduce footprint below the configured limit"
