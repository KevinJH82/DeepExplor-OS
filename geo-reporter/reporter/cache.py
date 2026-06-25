"""
Cache Layer — SQLite 搜索结果缓存
键：(country_code, lat_grid, lon_grid, category_id)
值：Tavily 原始文本列表（JSON 序列化）
有效期：30 天
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import List, Optional


CACHE_TTL = 30 * 24 * 3600  # 30 天（秒）
# 坐标网格精度：0.5 度（约 50km），同一网格内视为同一区域
GRID = 0.5


def _grid(val: float) -> float:
    """将坐标对齐到网格，避免微小偏差导致缓存未命中"""
    return round(round(val / GRID) * GRID, 4)


class CacheLayer:
    """Tavily 搜索结果的 SQLite 缓存"""

    def __init__(self, db_path: str = "./cache/geo_cache.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    texts     TEXT NOT NULL,
                    created   INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON search_cache(created)")

    def _key(self, country_code: str, lat: float, lon: float, category_id: str) -> str:
        return f"{country_code}|{_grid(lat)}|{_grid(lon)}|{category_id}"

    def get(self, country_code: str, lat: float, lon: float, category_id: str) -> Optional[List[str]]:
        """取缓存，过期返回 None"""
        key = self._key(country_code, lat, lon, category_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT texts, created FROM search_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        texts, created = row
        if time.time() - created > CACHE_TTL:
            self.delete(country_code, lat, lon, category_id)
            return None
        return json.loads(texts)

    def set(self, country_code: str, lat: float, lon: float, category_id: str, texts: List[str]):
        """写入缓存"""
        key = self._key(country_code, lat, lon, category_id)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (cache_key, texts, created) VALUES (?,?,?)",
                (key, json.dumps(texts, ensure_ascii=False), int(time.time()))
            )

    def delete(self, country_code: str, lat: float, lon: float, category_id: str):
        key = self._key(country_code, lat, lon, category_id)
        with self._conn() as conn:
            conn.execute("DELETE FROM search_cache WHERE cache_key = ?", (key,))

    def purge_expired(self):
        """清理所有过期条目"""
        cutoff = int(time.time()) - CACHE_TTL
        with self._conn() as conn:
            conn.execute("DELETE FROM search_cache WHERE created < ?", (cutoff,))
