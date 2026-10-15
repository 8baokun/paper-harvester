#!/usr/bin/env python3
"""文献排序模块——实现 SKILL.md 定义的五维权重排序。

权重：相关性 0.35 / 被引 0.25 / 时效 0.20 / 来源 0.10 / OA 0.10

这是修复 v0.1.0 "名实不符"问题（审核报告 P0-2）的实现：
SKILL.md 定义了排序权重但脚本未实现，现在补上。
"""
import sys, json, re
from datetime import datetime
from config import SORT_WEIGHTS, get_source_tier


def _score_relevance(paper: dict, query: str = "") -> float:
    """相关性分数：标题精确匹配 > 关键词匹配 > 摘要匹配。"""
    if not query:
        return 0.5  # 无查询词时给中等分
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    q = query.lower()

    if q in title:
        # 标题精确包含 → 0.8~1.0
        idx = title.index(q)
        # 标题开头匹配 > 中间匹配
        base = 1.0 if idx == 0 else 0.85
        # 标题完全等于查询词
        if title.strip() == q:
            base = 1.0
        return base
    elif q in abstract:
        return 0.5  # 摘要包含 → 0.5
    else:
        # 分词匹配
        words = re.split(r"\s+", q)
        matched = sum(1 for w in words if w in title or w in abstract)
        return min(0.3 + 0.15 * matched, 0.6) if matched else 0.2


def _score_citation(paper: dict, max_citations: int = 1000) -> float:
    """被引分数：被引次数归一化到 0~1（对数缩放）。"""
    count = paper.get("cited_by_count", 0) or 0
    if count <= 0:
        return 0.0
    if count >= max_citations:
        return 1.0
    # 对数缩放：10次=0.5, 100次=0.75, 500次=0.9
    import math
    return min(math.log10(count + 1) / math.log10(max_citations + 1), 1.0)


def _score_recency(paper: dict) -> float:
    """时效性分数：越新分数越高。"""
    year_str = str(paper.get("year", "") or "")[:4]
    try:
        year = int(year_str)
    except (ValueError, TypeError):
        return 0.3  # 无年份信息
    current_year = datetime.now().year
    age = current_year - year
    if age <= 0:
        return 1.0  # 当年或未来
    elif age <= 1:
        return 0.9
    elif age <= 3:
        return 0.8
    elif age <= 5:
        return 0.6
    elif age <= 10:
        return 0.4
    elif age <= 20:
        return 0.2
    else:
        return 0.1


def _score_source(paper: dict) -> float:
    """来源质量分数。"""
    journal = paper.get("source", "")
    return get_source_tier(journal)


def _score_oa(paper: dict) -> float:
    """开放获取程度分数。

    True → 1.0（真OA，直接可下载）
    "ncpssd_login" → 0.7（条件OA，需注册）
    False → 0.0（非OA）
    """
    oa = paper.get("oa", False)
    if oa is True:
        return 1.0
    elif oa == "ncpssd_login":
        return 0.7
    elif oa:
        # 其他字符串值视为条件OA
        return 0.5
    return 0.0


def compute_score(paper: dict, query: str = "") -> float:
    """计算文献的综合排序分数（0~1）。"""
    scores = {
        "relevance": _score_relevance(paper, query),
        "citation": _score_citation(paper),
        "recency": _score_recency(paper),
        "source": _score_source(paper),
        "oa": _score_oa(paper),
    }
    total = sum(scores[k] * SORT_WEIGHTS[k] for k in SORT_WEIGHTS)
    return round(total, 4)


def sort_papers(papers: list, query: str = "", descending: bool = True) -> list:
    """对文献列表按五维权重排序。

    Args:
        papers: 文献列表
        query: 检索关键词（用于相关性评分）
        descending: 降序排列（默认True，分数高的在前）
    Returns:
        排序后的文献列表，每篇增加 relevance_score 字段
    """
    scored = []
    for p in papers:
        score = compute_score(p, query)
        p["relevance_score"] = score
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=descending)
    return [p for _, p in scored]


# ── CLI ───────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="文献排序工具")
    parser.add_argument("query", nargs="?", default="", help="检索关键词（用于相关性评分）")
    parser.add_argument("--limit", type=int, default=0, help="返回数量（0=全部）")
    args = parser.parse_args()

    papers = json.load(sys.stdin)
    sorted_papers = sort_papers(papers, args.query)
    if args.limit > 0:
        sorted_papers = sorted_papers[:args.limit]
    print(json.dumps(sorted_papers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
