# app/services/db_service.py
import json
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List, Sequence

DB_PATH = Path("/data/HDF_Vision.db")

# SQL výraz na výpočet dnešného dňa podľa lokálneho času z primárneho timestampu ts_ms
_TODAY_LOCALDATE_SQL = "date(datetime(ts_ms / 1000.0, 'unixepoch', 'localtime')) = date('now','localtime')"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  draft_updated_at TIMESTAMP,
  published_at TIMESTAMP
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
  view_id TEXT,
  run_id TEXT,
  thumb_path TEXT,
  full_path TEXT,
  meta_json TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recipe_change_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL,
  recipe_id INTEGER,
  recipe_name TEXT NOT NULL,
  view_id TEXT,
  view_name TEXT,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  entity_name TEXT,
  field_name TEXT,
  old_value_json TEXT,
  new_value_json TEXT,
  source TEXT,
  details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_recipe_change_log_ts ON recipe_change_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_recipe_change_log_recipe ON recipe_change_log(recipe_name);
CREATE INDEX IF NOT EXISTS idx_recipe_change_log_view ON recipe_change_log(view_id);
CREATE INDEX IF NOT EXISTS idx_recipe_change_log_type ON recipe_change_log(entity_type);
"""

class DbService:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.recipes_base = self.db_path.parent / "recipes"
        self.recipes_base.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()
        self._table_columns_cache: dict[str, set[str]] = {}

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.executescript(SCHEMA)
        cur.execute("PRAGMA table_info(recipes)")
        columns = {row[1] for row in cur.fetchall()}
        if "draft_updated_at" not in columns:
            cur.execute("ALTER TABLE recipes ADD COLUMN draft_updated_at TIMESTAMP")
        if "published_at" not in columns:
            cur.execute("ALTER TABLE recipes ADD COLUMN published_at TIMESTAMP")
        cur.execute("PRAGMA table_info(results)")
        result_columns = {row[1] for row in cur.fetchall()}
        if "view_id" not in result_columns:
            cur.execute("ALTER TABLE results ADD COLUMN view_id TEXT")
        if "run_id" not in result_columns:
            cur.execute("ALTER TABLE results ADD COLUMN run_id TEXT")
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

    def mark_recipe_draft_updated(self, name: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE recipes SET draft_updated_at=CURRENT_TIMESTAMP WHERE name=?",
            (name,),
        )
        self._conn.commit()

    def mark_recipe_published(self, name: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE recipes SET published_at=CURRENT_TIMESTAMP WHERE name=?",
            (name,),
        )
        self._conn.commit()

    def recipe_publish_state(self, name: str) -> Dict[str, Optional[str]]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT draft_updated_at, published_at FROM recipes WHERE name=?",
            (name,),
        )
        row = cur.fetchone()
        if row is None:
            return {"draft_updated_at": None, "published_at": None}
        return {"draft_updated_at": row[0], "published_at": row[1]}

    def delete_recipe(self, name: str):
        cur = self._conn.cursor()
        cur.execute("DELETE FROM recipes WHERE name=?", (name,))
        self._conn.commit()

    # -------- recipe json (tools etc.) --------
    def _recipe_json_path(self, name: str) -> Path:
        return self.recipes_base / name / "recipe.json"

    def load_recipe_json(self, name: str) -> Dict[str, Any]:
        path = self._recipe_json_path(name)
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_recipe_json(self, name: str, data: Dict[str, Any]) -> Path:
        path = self._recipe_json_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)
        self._write_backup(name, serialized)
        return path

    def get_recipe_tools(self, name: str) -> List[Dict[str, Any]]:
        data = self.load_recipe_json(name)
        tools = data.get("tools", [])
        return list(tools) if isinstance(tools, list) else []

    def set_recipe_tools(self, name: str, tools: List[Dict[str, Any]]) -> Path:
        data = self.load_recipe_json(name)
        data["tools"] = list(tools)
        return self.save_recipe_json(name, data)

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
    def insert_result(
        self,
        ts_ms: int,
        recipe_id: int,
        ok: bool,
        metrics: Dict[str, Any],
        thumb_path: str,
        full_path: Optional[str],
        meta_json: str,
        *,
        view_id: str | None = None,
        run_id: str | None = None,
    ):
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO results(ts_ms, recipe_id, ok, ssim, blob_count, total_area, view_id, run_id, thumb_path, full_path, meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(ts_ms), int(recipe_id), 1 if ok else 0,
                float(metrics.get("ssim")) if "ssim" in metrics else None,
                int(metrics.get("blob_count")) if "blob_count" in metrics else None,
                int(metrics.get("total_area")) if "total_area" in metrics else None,
                str(view_id) if view_id else None,
                str(run_id) if run_id else None,
                thumb_path,
                full_path,
                meta_json,
            )
        )
        self._conn.commit()

    def daily_stats(self, recipe_id: int, view_id: str | None = None) -> Dict[str, Any]:
        cur = self._conn.cursor()
        query = (
            "SELECT "
            "    COUNT(*) AS total, "
            "    COALESCE(SUM(ok), 0) AS ok_count, "
            "    COALESCE(SUM(CASE WHEN ok THEN 0 ELSE 1 END), 0) AS nok_count, "
            "    COALESCE(SUM(CAST(json_extract(meta_json, '$.total_cycle_time_ms') AS REAL)), 0.0) AS total_cycle_time_ms "
            "FROM results "
            f"WHERE recipe_id=? AND {_TODAY_LOCALDATE_SQL}"
        )
        params: list[Any] = [recipe_id]
        if view_id:
            query += " AND view_id=?"
            params.append(str(view_id))
        total_cycle_time_ms = 0.0
        try:
            cur.execute(query, params)
            row = cur.fetchone() or (0, 0, 0, 0.0)
        except sqlite3.OperationalError:
            fallback_query = (
                "SELECT COUNT(*) AS total, COALESCE(SUM(ok), 0) AS ok_count, "
                "COALESCE(SUM(CASE WHEN ok THEN 0 ELSE 1 END), 0) AS nok_count "
                f"FROM results WHERE recipe_id=? AND {_TODAY_LOCALDATE_SQL}"
            )
            if view_id:
                fallback_query += " AND view_id=?"
            cur.execute(fallback_query, params)
            row = cur.fetchone() or (0, 0, 0)
        else:
            total_cycle_time_ms = float(row[3] or 0.0)

        total = int(row[0] or 0)
        ok = int(row[1] or 0)
        nok = int(row[2] or 0)
        yield_pct = (ok / total * 100.0) if total else 0.0
        return {
            "total": total,
            "ok": ok,
            "nok": nok,
            "yield": round(yield_pct, 2),
            "total_cycle_time_ms": total_cycle_time_ms,
        }
   
    def recent_results(
        self, recipe_id: int, limit: int = 12, *, view_id: str | None = None
    ):
        cur = self._conn.cursor()
        query = (
            "SELECT ts_ms, ok, thumb_path, full_path, ssim, blob_count, total_area, view_id, run_id "
            f"FROM results WHERE recipe_id=? AND {_TODAY_LOCALDATE_SQL}"
        )
        params: list[Any] = [recipe_id]
        if view_id:
            query += " AND view_id=?"
            params.append(str(view_id))
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        cur.execute(query, params)
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
                "view_id": r[7] if len(r) > 7 else None,
                "run_id": r[8] if len(r) > 8 else None,
            }
            for r in rows
        ]

    # -------- rich image helpers --------
    def _table_columns(self, table: str) -> set[str]:
        if table in self._table_columns_cache:
            return self._table_columns_cache[table]
        try:
            cur = self._conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            columns = {str(row[1]) for row in cur.fetchall()}
        except sqlite3.Error:
            columns = set()
        self._table_columns_cache[table] = columns
        return columns

    def _table_exists(self, table: str) -> bool:
        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            return cur.fetchone() is not None
        except sqlite3.Error:
            return False

    def _fetch_dicts(self, cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
        columns: Sequence[str] = [col[0] for col in cur.description] if cur.description else []
        rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def recent_image_records(
        self,
        recipe_id: int,
        *,
        limit: int = 12,
        tool_key: Optional[str] = None,
        view_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        records: list[dict[str, Any]] = []

        if self._table_exists("tool_runs"):
            columns = self._table_columns("tool_runs")
            cur = self._conn.cursor()
            query = "SELECT * FROM tool_runs WHERE recipe_id=?"
            params: list[Any] = [int(recipe_id)]
            key_column = None
            for candidate in ("tool_key", "tool_id", "tool_name"):
                if candidate in columns:
                    key_column = candidate
                    break
            if tool_key and key_column:
                query += f" AND {key_column}=?"
                params.append(str(tool_key))
            if view_id and "view_id" in columns:
                query += " AND view_id=?"
                params.append(str(view_id))
            if "created_at" in columns:
                query += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
            else:
                query += " ORDER BY id DESC LIMIT ?"
            params.append(int(limit) * 3)
            try:
                cur.execute(query, params)
                for row in self._fetch_dicts(cur):
                    records.append(self._normalize_tool_run_row(row))
            except sqlite3.Error:
                records.clear()

        if not records:
            fallback = self.recent_results(recipe_id, limit, view_id=view_id)
            for row in fallback:
                records.append(
                    {
                        "ts_ms": row.get("ts_ms"),
                        "ok": bool(row.get("ok", False)),
                        "status": "ok" if row.get("ok") else "nok",
                        "thumb_path": row.get("thumb"),
                        "full_path": row.get("full"),
                        "metrics": {
                            key: row.get(key)
                            for key in ("ssim", "blob_count", "total_area")
                            if row.get(key) is not None
                        },
                    }
                )

        # ensure deterministic order (most recent first)
        records.sort(key=lambda item: item.get("ts_ms") or 0, reverse=True)
        return records[: int(limit)]

    def _normalize_tool_run_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        normalized: dict[str, Any] = {}
        normalized["id"] = row.get("id")
        normalized["ts_ms"] = row.get("ts_ms") or row.get("timestamp_ms")
        normalized["ok"] = bool(row.get("ok")) if "ok" in row else None
        status_value = (
            row.get("status")
            or row.get("result_status")
            or ("ok" if normalized.get("ok") else "nok" if normalized.get("ok") is not None else None)
        )
        normalized["status"] = status_value
        normalized["tool_key"] = row.get("tool_key") or row.get("tool_id") or row.get("tool_name")
        normalized["thumb_path"] = row.get("thumb_path") or row.get("thumbnail_path")
        normalized["full_path"] = row.get("full_path") or row.get("image_path")
        normalized["overlay_path"] = row.get("overlay_image_path") or row.get("overlay_path")
        normalized["aligned_path"] = row.get("aligned_image_path") or row.get("aligned_path")
        normalized["raw_path"] = row.get("image_path") or row.get("raw_image_path") or row.get("full_path")
        normalized["meta_path"] = row.get("meta_path") or row.get("meta_json_path")

        metrics: dict[str, Any] = {}
        for key in ("ssim", "blob_count", "total_area"):
            if row.get(key) is not None:
                metrics[key] = row.get(key)

        for key in ("metrics", "metrics_json", "metrics_payload"):
            value = row.get(key)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        metrics.update(parsed)
                except Exception:
                    pass
            elif isinstance(value, dict):
                metrics.update(value)

        normalized["metrics"] = metrics

        meta_json = row.get("meta_json")
        if isinstance(meta_json, str):
            normalized["meta_json"] = meta_json
        elif isinstance(meta_json, (bytes, bytearray)):
            try:
                normalized["meta_json"] = meta_json.decode("utf-8")
            except Exception:
                pass

        return normalized

    def export_csv_today(self, recipe_id: int, out_path: str):
        import csv
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ts_ms, ok, ssim, blob_count, total_area, thumb_path, full_path, meta_json "
            f"FROM results WHERE recipe_id=? AND {_TODAY_LOCALDATE_SQL} "
            "ORDER BY ts_ms ASC",
            (recipe_id,),
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

    def _write_backup(self, name: str, serialized: str) -> None:
        try:
            backup_dir = self.db_path.parent / "tmp" / "recipes"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{name}_draft.json"
            with open(backup_path, "w", encoding="utf-8") as backup_file:
                backup_file.write(serialized)
        except Exception as exc:
            print(f"[DbService] Failed to write draft backup for {name}: {exc}")

    def _ensure_indices(self):
        cur = self._conn.cursor()
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_results_recipe ON results(recipe_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_results_created ON results(created_at)")
            self._conn.commit()
        except Exception:
            pass
