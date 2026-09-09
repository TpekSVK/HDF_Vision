"""Read-only history queries; each job owns its SQLite connection."""
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def query_results(path, filters, before=None, limit=51):
    where = ['r.ts_ms >= ?', 'r.ts_ms < ?']
    args = [filters['start'], filters['end']]
    for key, column in [('recipe', 'p.name'), ('view', 'r.view_id'), ('ok', 'r.ok')]:
        if filters.get(key) is not None and filters[key] != '':
            where.append(column + ' = ?')
            args.append(filters[key])
    if before is not None:
        where.append('r.id < ?')
        args.append(before)
    connection = _connect(path)
    try:
        rows = connection.execute(
            'SELECT r.*, p.name AS recipe FROM results r LEFT JOIN recipes p ON p.id=r.recipe_id '
            'WHERE ' + ' AND '.join(where) + ' ORDER BY r.id DESC LIMIT ?', args + [limit]
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def metadata(row):
    try:
        value = json.loads(row.get('meta_json') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def filter_options(path):
    connection = _connect(path)
    try:
        return ([row[0] for row in connection.execute('SELECT name FROM recipes ORDER BY name')],
                [row[0] for row in connection.execute(
                    "SELECT DISTINCT view_id FROM results WHERE view_id IS NOT NULL ORDER BY view_id")])
    finally:
        connection.close()


def export_results(path, filters, destination):
    # Exclusive creation prevents accidentally replacing an existing export.
    count, before = 0, None
    with open(destination, 'x', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['ID', 'Čas', 'Recept', 'Pohľad', 'Stav', 'Cyklus ms', 'Run ID', 'Metriky nástrojov'])
        while True:
            rows = query_results(path, filters, before, 200)
            if not rows:
                break
            for row in rows:
                meta = metadata(row)
                values = [row['id'], datetime.fromtimestamp(row['ts_ms']/1000).isoformat(' ', timespec='seconds'),
                          row['recipe'], row['view_id'], 'OK' if row['ok'] else 'NOK',
                          meta.get('cycle_time_ms'), row['run_id'],
                          json.dumps(meta.get('per_tool', []), ensure_ascii=False)]
                writer.writerow([("'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r'))
                                  else value) for value in values])
                count += 1
            before = rows[-1]['id']
    return count
