import os, time
from datetime import datetime, timedelta

BASE="/data"
DAYS=7
MAX_BYTES=5*1024*1024*1024  # 5 GB

def _iter_files(root):
    for dp,_,fns in os.walk(root):
        for fn in fns:
            yield os.path.join(dp, fn)

def delete_older_than(days=DAYS):
    cutoff = time.time() - days*24*3600
    for f in _iter_files(BASE):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except Exception:
            pass

def enforce_size_quota(max_bytes=MAX_BYTES):
    files = list(_iter_files(BASE))
    files = [(f, os.path.getmtime(f), os.path.getsize(f)) for f in files]
    total = sum(sz for _,_,sz in files)
    if total <= max_bytes: return
    # zoradenie od najstarších
    files.sort(key=lambda x: x[1])
    for f,_,sz in files:
        try:
            os.remove(f)
            total -= sz
            if total <= max_bytes: break
        except Exception:
            pass

def run_retention_cycle():
    delete_older_than(DAYS)
    enforce_size_quota(MAX_BYTES)

import threading

class RetentionThread:
    def __init__(self, interval_sec=300):  # 5 min namiesto 20 s
        self.interval = interval_sec
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        if not self._th.is_alive():
            self._stop.clear()
            self._th = threading.Thread(target=self._loop, daemon=True)
            self._th.start()

    def stop(self):
        self._stop.set()
        if self._th.is_alive():
            self._th.join(timeout=1.0)

    def _loop(self):
        while not self._stop.is_set():
            try:
                run_retention_cycle()
            except Exception as e:
                print(f"[Retention] Exception: {e}")
            self._stop.wait(self.interval)