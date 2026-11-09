"""Read-only, deterministic history across independent station databases."""
import csv
import json
from datetime import datetime

from app.services.results_browser import _connect, filter_options, metadata


def history_cursor(row):
    return row['ts_ms'], row['station_id'], row['id']


def query_history(sources, filters, before=None, limit=9):
    rows = []
    for source in sources:
        where = ['r.ts_ms >= ?', 'r.ts_ms < ?']
        args = [filters['start'], filters['end']]
        for key, column in [('recipe', 'p.name'), ('view', 'r.view_id'), ('ok', 'r.ok')]:
            if filters.get(key) not in (None, ''):
                where.append(column + ' = ?')
                args.append(filters[key])
        if before is not None:
            timestamp, station_id, ident = before
            # Global ordering: timestamp, station id, local result id (all DESC).
            if source['id'] == station_id:
                where.append('(r.ts_ms < ? OR (r.ts_ms = ? AND r.id < ?))')
                args.extend([timestamp, timestamp, ident])
            else:
                where.append('r.ts_ms ' + ('<= ?' if source['id'] < station_id else '< ?'))
                args.append(timestamp)
        connection = _connect(source['db_path'])
        try:
            found = connection.execute(
                'SELECT r.*, p.name AS recipe FROM results r LEFT JOIN recipes p ON p.id=r.recipe_id '
                'WHERE ' + ' AND '.join(where) + ' ORDER BY r.ts_ms DESC, r.id DESC LIMIT ?',
                args + [limit]).fetchall()
            rows.extend(dict(row, station_id=source['id'], station_name=source['name']) for row in found)
        finally:
            connection.close()
    return sorted(rows, key=history_cursor, reverse=True)[:limit]


def history_options(sources):
    recipes, views = set(), set()
    for source in sources:
        names, ids = filter_options(source['db_path'])
        recipes.update(names)
        views.update(ids)
    return sorted(recipes), sorted(views)


def export_history(sources, filters, destination):
    count, before = 0, None
    with open(destination, 'x', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Kamera', 'ID', 'Čas', 'Recept', 'Pohľad', 'Stav', 'Cyklus ms', 'Run ID', 'Metriky nástrojov'])
        while True:
            rows = query_history(sources, filters, before, 200)
            if not rows:
                return count
            for row in rows:
                meta = metadata(row)
                values = [row['station_name'], row['id'],
                          datetime.fromtimestamp(row['ts_ms']/1000).isoformat(' ', timespec='seconds'),
                          row['recipe'], meta.get('view_name') or row['view_id'], 'OK' if row['ok'] else 'NOK',
                          meta.get('cycle_time_ms'), row['run_id'],
                          json.dumps(meta.get('per_tool', []), ensure_ascii=False)]
                writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r'))
                                 else value for value in values])
                count += 1
            before = history_cursor(rows[-1])
