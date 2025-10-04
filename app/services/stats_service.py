# app/services/stats_service.py
from app.services.db_service import DbService

class StatsService:
    def __init__(self, db: DbService | None = None):
        self.db = db or DbService()

    def daily_for_recipe(self, recipe: str):
        rid = self.db.recipe_id(recipe)
        if rid is None:
            return {"total": 0, "ok": 0, "nok": 0, "yield": 0.0}
        return self.db.daily_stats(rid)
