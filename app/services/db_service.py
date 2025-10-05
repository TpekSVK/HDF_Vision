# app/services/db_service.py
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List

DB_PATH = Path("/data/HDF_Vision.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS thresholds (
  recipe_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY (recipe_id, key),
  FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL,
  recipe_id INTEGER NOT NULL,
  ok INTEGER NOT NULL,
  ssim REAL,
  blob_count INTEGER,
  total_area INTEGER,
  thumb_path TEXT,
  full_path TEXT,
  meta_json TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
"""

class DbService:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        self._conn.commit()

    def conn(self):
        return self._conn

    # -------- recipes --------
    def ensure_recipe(self, name: str) -> int:
        cur = self._conn.cursor()
        cur.execute("INSERT OR IGNORE INTO recipes(name) VALUES (?)", (name,))
        self._conn.commit()
        cur.execute("SELECT id FROM recipes WHERE name=?", (name,))
        rid = cur.fetchone()[0]
        return rid

    def list_recipes(self) -> list[str]:
        cur = self._conn.cursor()
        cur.execute("SELECT name FROM recipes ORDER BY name")
        return [r[0] for r in cur.fetchall()]

    def rename_recipe(self, old: str, new: str):
        cur = self._conn.cursor()
        cur.execute("UPDATE recipes SET name=? WHERE name=?", (new, old))
        self._conn.commit()

    def delete_recipe(self, name: str):
        cur = self._conn.cursor()
        cur.execute("DELETE FROM recipes WHERE name=?", (name,))
        self._conn.commit()

    def recipe_id(self, name: str) -> Optional[int]:
        cur = self._conn.cursor()
        cur.execute("SELECT id FROM recipes WHERE name=?", (name,))
        r = cur.fetchone()
        return r[0] if r else None

    # -------- thresholds --------
    def set_thresholds(self, recipe_id: int, th: Dict[str, float]):
        cur = self._conn.cursor()
        for k, v in th.items():
            cur.execute(
                "INSERT INTO thresholds(recipe_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(recipe_id, key) DO UPDATE SET value=excluded.value",
                (recipe_id, k, float(v)),
            )
        self._conn.commit()

    def get_thresholds(self, recipe_id: int) -> Dict[str, float]:
        cur = self._conn.cursor()
        cur.execute("SELECT key, value FROM thresholds WHERE recipe_id=?", (recipe_id,))
        return {k: float(v) for (k, v) in cur.fetchall()}

    # -------- results --------
    def insert_result(self, ts_ms: int, recipe_id: int, ok: bool, metrics: Dict[str, Any],
                      thumb_path: str, full_path: Optional[str], meta_json: str):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO results(ts_ms, recipe_id, ok, ssim, blob_count, total_area, thumb_path, full_path, meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                int(ts_ms), int(recipe_id), 1 if ok else 0,
                float(metrics.get("ssim")) if "ssim" in metrics else None,
                int(metrics.get("blob_count")) if "blob_count" in metrics else None,
                int(metrics.get("total_area")) if "total_area" in metrics else None,
                thumb_path, full_path, meta_json,
            )
        )
        self._conn.commit()

    def daily_stats(self, recipe_id: int) -> Dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*), SUM(ok), COUNT(*)-SUM(ok) FROM results "
            "WHERE recipe_id=? AND date(created_at)=date('now','localtime')",
            (recipe_id,)
        )
        row = cur.fetchone()
        total = row[0] or 0
        ok = row[1] or 0
        nok = row[2] or 0
        yield_pct = (ok / total * 100.0) if total else 0.0
        return {"total": total, "ok": ok, "nok": nok, "yield": round(yield_pct, 2)}
   
    def recent_results(self, recipe_id: int, limit: int = 12):
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ts_ms, ok, thumb_path, full_path, ssim, blob_count, total_area "
            "FROM results WHERE recipe_id=? AND date(created_at)=date('now','localtime') "
            "ORDER BY id DESC LIMIT ?",
            (recipe_id, int(limit))
        )
        rows = cur.fetchall()
        return [
            {
                "ts_ms": r[0],
                "ok": bool(r[1]),
                "thumb": r[2],
                "full": r[3],
                "ssim": r[4],
                "blob_count": r[5],
                "total_area": r[6],
            }
            for r in rows
        ]

    def export_csv_today(self, recipe_id: int, out_path: str):
        import csv
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ts_ms, ok, ssim, blob_count, total_area, thumb_path, full_path, meta_json "
            "FROM results WHERE recipe_id=? AND date(created_at)=date('now','localtime') "
            "ORDER BY id ASC",
            (recipe_id,)
        )
        rows = cur.fetchall()
        # istota: vytvor priečinok
        import os
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts_ms","ok","ssim","blob_count","total_area","thumb_path","full_path","meta_json"])
            for r in rows:
                w.writerow(r)
        return out_path
