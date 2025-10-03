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
