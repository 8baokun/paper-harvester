#!/usr/bin/env python3
"""搜索结果缓存——基于 SQLite 的 TTL 缓存。

相同 query+source+limit 在 TTL（默认7天）内直接返回缓存结果，
避免重复请求外部 API，降低限流风险。

缓存策略：
- SQLite 存储，零依赖（Python 内置 sqlite3）
- key = (source, query, limit) 的哈希
- TTL 过期自动清理
- 支持 --no-cache 跳过缓存（环境变量 PH_NO_CACHE=1 也可跳过）

用法（编程式）:
    from cache import SearchCache

    cache = SearchCache()
    # 写入缓存
    cache.put("ncpssd", "业法财融合", 20, papers)
    # 读取缓存
    cached = cache.get("ncpssd", "业法财融合", 20)
    if cached is not None:
        print("命中缓存", len(cached))
    # 清理过期
    cache.cleanup()

用法（CLI）:
    # 查看缓存统计
    python cache.py --stats

    # 清理过期缓存
    python cache.py --cleanup

    # 清空所有缓存
    python cache.py --clear
"""
import sys, os, json, sqlite3, hashlib, time, argparse
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from config import CACHE_DB_PATH, CACHE_TTL


def _make_key(source: str, query: str, limit: int) -> str:
    """生成缓存 key 的哈希。"""
    raw = f"{source}|{query.strip().lower()}|{limit}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SearchCache:
    """搜索结果缓存。

    基于 SQLite，支持 TTL 自动过期。
    """

    def __init__(self, db_path: str = CACHE_DB_PATH, ttl: int = CACHE_TTL):
        """初始化缓存。

        Args:
            db_path: SQLite 数据库路径
            ttl: 缓存有效期（秒），默认 7 天
        """
        self.db_path = db_path
        self.ttl = ttl
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    query TEXT NOT NULL,
                    limit_val INTEGER,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    result_count INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_query
                ON search_cache(source, query)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON search_cache(created_at)
            """)

    def get(self, source: str, query: str, limit: int) -> Optional[List[dict]]:
        """读取缓存。

        Returns:
            缓存的文献列表，或 None（缓存未命中/已过期）
        """
        key = _make_key(source, query, limit)
        now = time.time()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data, created_at FROM search_cache WHERE key = ?",
                (key,)
            ).fetchone()

        if row is None:
            return None

        data_str, created_at = row
        if now - created_at > self.ttl:
            # 已过期
            return None

        try:
            return json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            return None

    def put(self, source: str, query: str, limit: int,
            papers: List[dict]) -> bool:
        """写入缓存。

        Returns:
            True 如果写入成功
        """
        if not papers:
            return False

        key = _make_key(source, query, limit)
        data_str = json.dumps(papers, ensure_ascii=False)
        now = time.time()

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO search_cache
                       (key, source, query, limit_val, data, created_at, result_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (key, source, query.strip(), limit, data_str, now, len(papers))
                )
            return True
        except sqlite3.Error as e:
            print(f"[缓存] 写入失败: {e}", file=sys.stderr)
            return False

    def cleanup(self) -> int:
        """清理过期缓存。

        Returns:
            清理的条目数
        """
        now = time.time()
        cutoff = now - self.ttl
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM search_cache WHERE created_at < ?",
                (cutoff,)
            )
            removed = cursor.rowcount
        return removed

    def clear(self) -> int:
        """清空所有缓存。

        Returns:
            清除的条目数
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM search_cache")
            removed = cursor.rowcount
        return removed

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息。"""
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM search_cache WHERE created_at < ?",
                (now - self.ttl,)
            ).fetchone()[0]
            by_source = conn.execute(
                "SELECT source, COUNT(*), SUM(result_count) FROM search_cache GROUP BY source"
            ).fetchall()

        return {
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired,
            "by_source": {
                row[0]: {"entries": row[1], "total_papers": row[2] or 0}
                for row in by_source
            },
            "db_path": self.db_path,
            "ttl_seconds": self.ttl,
        }


# ── 全局缓存实例（惰性初始化）──────────────────────────
_cache_instance: Optional[SearchCache] = None


def get_cache() -> Optional[SearchCache]:
    """获取全局缓存实例。

    如果环境变量 PH_NO_CACHE=1 则返回 None（禁用缓存）。
    """
    global _cache_instance
    if os.environ.get("PH_NO_CACHE", "0") == "1":
        return None
    if _cache_instance is None:
        _cache_instance = SearchCache()
    return _cache_instance


def cached_search(source: str, query: str, limit: int,
                  search_fn, force_refresh: bool = False) -> List[dict]:
    """带缓存的搜索包装器。

    Args:
        source: 检索源名称（如 "ncpssd", "openalex"）
        query: 检索关键词
        limit: 结果数量限制
        search_fn: 搜索函数，签名为 search_fn() -> List[dict]
        force_refresh: True 强制刷新缓存

    Returns:
        检索结果列表
    """
    cache = get_cache()

    # 尝试读缓存
    if cache and not force_refresh:
        cached = cache.get(source, query, limit)
        if cached is not None:
            print(f"[缓存] 命中: {source}「{query}」({len(cached)} 条)",
                  file=sys.stderr)
            return cached

    # 执行搜索
    papers = search_fn()

    # 写入缓存
    if cache and papers:
        cache.put(source, query, limit, papers)

    return papers


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="搜索缓存管理工具")
    parser.add_argument("--stats", action="store_true", help="查看缓存统计")
    parser.add_argument("--cleanup", action="store_true", help="清理过期缓存")
    parser.add_argument("--clear", action="store_true", help="清空所有缓存")
    args = parser.parse_args()

    if not any([args.stats, args.cleanup, args.clear]):
        args.stats = True  # 默认显示统计

    cache = SearchCache()

    if args.clear:
        removed = cache.clear()
        print(f"已清空 {removed} 条缓存")
        return

    if args.cleanup:
        removed = cache.cleanup()
        print(f"已清理 {removed} 条过期缓存")
        return

    if args.stats:
        stats = cache.stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
