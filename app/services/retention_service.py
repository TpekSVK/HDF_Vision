# app/services/retention_service.py
import os, json, time
from pathlib import Path
from datetime import datetime, timedelta

from app.services.storage_service import CFG

class RetentionService:
    """
    Politika:
      1) Vymaž priečinky /data/runs/YYYYMMDD staršie ako retention_days.
      2) Ak /data > retention_max_gb, maž najstaršie súbory z /data/runs FIFO (thumbs,full,meta).
      3) Nikdy nemaž /data/recipes a /data/validation.
    """
    def __init__(self, base="/data"):
        self.base = Path(base)

    def run_once(self, verbose=True):
        days = int(CFG.get("retention_days", 7))
        max_gb = float(CFG.get("retention_max_gb", 5))
        self._delete_old_days(days, verbose)
        self._enforce_size(max_gb, verbose)

    # --- helpers ---
    def _delete_old_days(self, days, verbose):
        cutoff = datetime.now() - timedelta(days=days)
        runs = self.base / "runs"
        if not runs.exists(): return
        for d in sorted(runs.iterdir()):
            if not d.is_dir(): continue
            # očakávame YYYYMMDD
            try:
                dt = datetime.strptime(d.name, "%Y%m%d")
            except ValueError:
                continue
            if dt < cutoff:
                if verbose: print("[retention] rm old day:", d)
                self._rm_tree(d)

    def _enforce_size(self, max_gb, verbose):
        # prepočítaj veľkosť /data/runs
        runs = self.base / "runs"
        if not runs.exists(): return
        def dir_size(p: Path):
            s = 0
            for root, _, files in os.walk(p):
                for f in files:
                    try:
                        s += (Path(root)/f).stat().st_size
                    except Exception:
                        pass
            return s
        size = dir_size(runs) / (1024**3)
        if size <= max_gb: 
            if verbose: print(f"[retention] size OK: {size:.2f} GB <= {max_gb} GB")
            return

        # FIFO mazaníe – zoradíme všetky súbory podľa mtime (najstaršie prvé)
        all_files = []
        for root, _, files in os.walk(runs):
            for f in files:
                p = Path(root)/f
                try:
                    all_files.append((p.stat().st_mtime, p))
                except Exception:
                    pass
        all_files.sort(key=lambda x: x[0])  # oldest first

        idx = 0
        while size > max_gb and idx < len(all_files):
            p = all_files[idx][1]
            try:
                if verbose: print("[retention] rm", p)
                sz = p.stat().st_size
                p.unlink(missing_ok=True)
                size -= sz / (1024**3)
            except Exception:
                pass
            idx += 1

    def _rm_tree(self, p: Path):
        # rmdir -r (safe)
        for root, dirs, files in os.walk(p, topdown=False):
            for f in files:
                try: (Path(root)/f).unlink(missing_ok=True)
                except Exception: pass
            for d in dirs:
                try: (Path(root)/d).rmdir()
                except Exception: pass
        try: p.rmdir()
        except Exception: pass
