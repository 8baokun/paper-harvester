#!/usr/bin/env python3
"""反思循环搜索——首次结果不足时自动拆分关键词补充搜索。

当首次搜索结果不足（< min_results 条）时，自动：
1. 将原始查询拆分为更细粒度的子查询（分词/同义词扩展）
2. 执行补充搜索
3. 合并去重

拆分策略：
- 中文：按语义切分（去"的""与"等虚词，保留实词短语）
- 英文：按空格拆分，取词对组合
- 通用：移除常见停用词

用法（编程式）:
    from reflect import reflective_search

    results = reflective_search(
        query="业法财融合",
        search_fn=lambda q, l: search_ncpssd(q, l),
        source_name="ncpssd",
        min_results=5,
    )

用法（CLI）:
    # 反思搜索（需要指定搜索源脚本）
    python reflect.py "业法财融合" --source ncpssd --min-results 5

    # 指定搜索上限
    python reflect.py "自甘风险" --source ncpssd --limit 20 --min-results 10
"""
import sys, json, re, argparse, os, subprocess
from typing import List, Callable, Optional
from datetime import datetime

from dedup import dedup_papers, normalize_title
from config import REFLECT_MIN_RESULTS, REFLECT_MAX_EXPANSIONS, REFLECT_SUB_LIMIT


# ── 中文停用词 ────────────────────────────────────────
_CN_STOPWORDS = {
    "的", "与", "和", "及", "或", "在", "中", "为", "了", "对",
    "研究", "分析", "视角", "视角下", "基于", "以", "从",
    "the", "a", "an", "of", "and", "or", "in", "on", "for",
    "with", "to", "from", "by", "at", "as", "is", "are",
}


def split_query(query: str) -> List[str]:
    """将查询拆分为子查询。

    拆分策略：
    1. 按标点/空格切分
    2. 过滤停用词和过短的词
    3. 生成词对组合（2-gram）
    4. 保留原始查询中可能被忽略的关键词

    Returns:
        子查询列表（不含原始查询，已去重）
    """
    query = query.strip()
    if not query:
        return []

    # 按标点、空格切分
    parts = re.split(r"[\s,，;；、|/]+", query)
    parts = [p.strip() for p in parts if p.strip()]

    # 过滤停用词和过短词
    filtered = [p for p in parts if p.lower() not in _CN_STOPWORDS and len(p) >= 2]

    if not filtered:
        return []

    expansions = []

    # 策略1：单个实词
    if len(filtered) > 1:
        for word in filtered:
            if word not in expansions:
                expansions.append(word)

    # 策略2：相邻词对（bigram）
    if len(filtered) > 2:
        for i in range(len(filtered) - 1):
            pair = filtered[i] + " " + filtered[i + 1]
            if pair not in expansions:
                expansions.append(pair)

    # 策略3：首尾词对
    if len(filtered) > 2:
        pair = filtered[0] + " " + filtered[-1]
        if pair not in expansions:
            expansions.append(pair)

    return expansions[:REFLECT_MAX_EXPANSIONS]


def reflective_search(query: str,
                      search_fn: Callable[[str, int], List[dict]],
                      source_name: str = "unknown",
                      limit: int = 20,
                      min_results: int = REFLECT_MIN_RESULTS,
                      sub_limit: int = REFLECT_SUB_LIMIT,
                      verbose: bool = True) -> List[dict]:
    """反思循环搜索。

    首次搜索结果不足时自动拆分关键词补充搜索。

    Args:
        query: 原始检索关键词
        search_fn: 搜索函数，签名 search_fn(query, limit) -> List[dict]
        source_name: 检索源名称（用于日志）
        limit: 首次搜索的结果上限
        min_results: 最少结果数阈值，不足时触发反思
        sub_limit: 补充搜索每个子查询的结果上限
        verbose: 打印日志

    Returns:
        合并去重后的检索结果
    """
    # 首次搜索
    if verbose:
        print(f"[反思] 首次搜索: {source_name}「{query}」 (limit={limit})",
              file=sys.stderr)
    results = search_fn(query, limit) or []

    if verbose:
        print(f"[反思] 首次结果: {len(results)} 条 (阈值 {min_results})",
              file=sys.stderr)

    if len(results) >= min_results:
        return results

    # 结果不足，启动反思循环
    expansions = split_query(query)
    if verbose:
        print(f"[反思] 结果不足，拆分为 {len(expansions)} 个子查询: {expansions}",
              file=sys.stderr)

    if not expansions:
        return results

    # 逐个执行补充搜索
    all_results = list(results)
    for i, sub_query in enumerate(expansions):
        if len(all_results) >= min_results * 3:
            # 已经足够多了，停止补充
            if verbose:
                print(f"[反思] 结果已充分 ({len(all_results)} 条)，停止补充",
                      file=sys.stderr)
            break

        if verbose:
            print(f"[反思] 补充搜索 {i+1}/{len(expansions)}: 「{sub_query}」",
                  file=sys.stderr)

        try:
            sub_results = search_fn(sub_query, sub_limit) or []
            if verbose:
                print(f"[反思] 子查询「{sub_query}」返回 {len(sub_results)} 条",
                      file=sys.stderr)
            all_results.extend(sub_results)
        except Exception as e:
            if verbose:
                print(f"[反思] 子查询「{sub_query}」失败: {e}", file=sys.stderr)

    # 去重
    before_dedup = len(all_results)
    deduped = dedup_papers(all_results, verbose=verbose)

    if verbose:
        print(f"[反思] {source_name}「{query}」最终结果: {len(deduped)} 条 "
              f"(首次 {len(results)} + 补充 → 合并去重 {before_dedup} → {len(deduped)})",
              file=sys.stderr)

    return deduped


def _run_search_script(source: str, query: str, limit: int,
                       script_dir: str) -> List[dict]:
    """执行搜索脚本（子进程方式）。

    Args:
        source: 检索源名称
        query: 检索关键词
        limit: 结果数量
        script_dir: 脚本目录路径

    Returns:
        检索结果列表
    """
    script_map = {
        "ncpssd": "search_ncpssd.py",
        "openalex": "search_openalex.py",
        "s2": "search_semantic_scholar.py",
        "semantic": "search_semantic_scholar.py",
        "crossref": "search_crossref.py",
        "wechat": "search_wechat.py",
    }

    script_name = script_map.get(source)
    if not script_name:
        print(f"[反思] 未知检索源: {source}", file=sys.stderr)
        return []

    script_path = os.path.join(script_dir, script_name)
    if not os.path.exists(script_path):
        print(f"[反思] 脚本不存在: {script_path}", file=sys.stderr)
        return []

    try:
        result = subprocess.run(
            [sys.executable, script_path, query, str(limit)],
            capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
        if result.returncode != 0:
            print(f"[反思] 脚本执行失败: {result.stderr[:200]}", file=sys.stderr)
            return []
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[反思] 脚本执行异常: {e}", file=sys.stderr)
        return []


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="反思循环搜索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # NCPSSD 反思搜索
  python reflect.py "业法财融合" --source ncpssd --min-results 5

  # 指定搜索上限
  python reflect.py "自甘风险" --source ncpssd --limit 20 --min-results 10
        """)
    parser.add_argument("query", help="检索关键词")
    parser.add_argument("--source", "-s", default="ncpssd",
                        help="检索源 (ncpssd/openalex/s2/crossref/wechat)")
    parser.add_argument("--limit", "-l", type=int, default=20,
                        help="首次搜索结果上限 (默认 20)")
    parser.add_argument("--min-results", "-m", type=int,
                        default=REFLECT_MIN_RESULTS,
                        help=f"触发反思的最少结果数 (默认 {REFLECT_MIN_RESULTS})")
    parser.add_argument("--sub-limit", type=int, default=REFLECT_SUB_LIMIT,
                        help=f"补充搜索每次的结果上限 (默认 {REFLECT_SUB_LIMIT})")
    parser.add_argument("--output", "-o", help="保存为 JSON 文件")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))

    results = reflective_search(
        query=args.query,
        search_fn=lambda q, l: _run_search_script(args.source, q, l, script_dir),
        source_name=args.source,
        limit=args.limit,
        min_results=args.min_results,
        sub_limit=args.sub_limit,
        verbose=True,
    )

    output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[反思] 结果已保存: {args.output} ({len(results)} 条)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
