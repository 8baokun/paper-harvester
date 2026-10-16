#!/usr/bin/env python3
"""跨源去重模块——DOI 精确匹配 + 标题归一化 + 近重检测。

三级去重策略：
1. DOI 精确匹配：相同 DOI → 同一篇
2. 标题归一化精确匹配：去标点/空格/大小写后相同 → 同一篇
3. 标题 Jaccard 相似度 ≥ 0.8 → 近重（同一篇的不同版本）

合并策略：保留信息最全的记录（字段非空数最多），合并缺失字段。

用法:
  # 从 JSON 文件去重
  cat results.json | python dedup.py --query "业法财融合"

  # 指定相似度阈值
  cat results.json | python dedup.py --threshold 0.85

  # 从文件读取
  python dedup.py -i results.json -o deduped.json
"""
import sys, json, re, argparse, os
from typing import List, Dict, Tuple, Set

from config import DEDUP_SIMILARITY_THRESHOLD


def normalize_title(title: str) -> str:
    """标题归一化：移除标点、空格、大小写差异。

    "Self-Organization in Law: A Systems Perspective" → "selforganizationinlawasystemsperspective"
    "业法财融合：规范构造与制度实现" → "业法财融合规范构造与制度实现"
    """
    if not title:
        return ""
    t = title.lower().strip()
    # 移除所有标点和符号（中英文）
    t = re.sub(r"[\s\-_—–·•:：;；!！?？.。,，/\\|\"'`(){}\[\]<>《》「」『』【】]", "", t)
    return t


def _extract_doi(paper: dict) -> str:
    """提取并归一化 DOI。"""
    doi = paper.get("doi", "") or ""
    return doi.lower().strip().lstrip("https://doi.org/").lstrip("http://doi.org/")


def _word_set(title: str) -> Set[str]:
    """将标题拆分为词集合（用于 Jaccard 相似度）。

    中文按字拆分，英文按空格拆分。
    """
    normalized = normalize_title(title)
    if not normalized:
        return set()
    # 判断是否包含中文字符
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", normalized))
    if has_chinese:
        # 中文按字拆分（bigram 提高准确性）
        return set(normalized[i:i+2] for i in range(len(normalized) - 1)) if len(normalized) > 1 else {normalized}
    else:
        # 英文按词拆分（空格已在 normalize 中移除，用 3-gram）
        return set(normalized[i:i+3] for i in range(len(normalized) - 2)) if len(normalized) > 2 else {normalized}


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Jaccard 相似度：|A ∩ B| / |A ∪ B|。"""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _field_completeness(paper: dict) -> int:
    """计算文献记录的字段完整度（非空字段数）。"""
    count = 0
    for key in ("title", "authors", "year", "source", "doi", "url", "abstract", "pdf"):
        val = paper.get(key, "")
        if val and str(val).strip():
            count += 1
    return count


def _merge_papers(primary: dict, secondary: dict) -> dict:
    """合并两条文献记录，保留信息最全的。

    primary 是主记录，secondary 的字段在 primary 为空时补充。
    """
    merged = dict(primary)
    for key, val in secondary.items():
        if not merged.get(key) and val:
            merged[key] = val
    # 取被引次数较大值
    cited_p = primary.get("cited_by_count", 0) or 0
    cited_s = secondary.get("cited_by_count", 0) or 0
    merged["cited_by_count"] = max(cited_p, cited_s)
    # 记录合并来源
    dup_sources = set()
    if primary.get("_dedup_sources"):
        dup_sources.update(primary["_dedup_sources"])
    else:
        dup_sources.add(primary.get("search_engine", primary.get("source", "unknown")))
    dup_sources.add(secondary.get("search_engine", secondary.get("source", "unknown")))
    merged["_dedup_sources"] = sorted(dup_sources)
    return merged


def dedup_papers(papers: List[dict],
                 threshold: float = DEDUP_SIMILARITY_THRESHOLD,
                 verbose: bool = False) -> List[dict]:
    """跨源去重。

    Args:
        papers: 文献列表
        threshold: Jaccard 相似度阈值（≥ threshold 视为近重）
        verbose: 打印去重日志

    Returns:
        去重后的文献列表，保留信息最全的记录
    """
    if not papers:
        return []

    # 三级去重
    # 第一级：DOI 精确匹配
    doi_map: Dict[str, int] = {}  # doi → index in result
    # 第二级：标题归一化精确匹配
    title_map: Dict[str, int] = {}  # normalized title → index in result
    # 第三级：标题词集合（用于近重检测）
    word_sets: List[Set[str]] = []

    result: List[dict] = []

    for paper in papers:
        doi = _extract_doi(paper)
        norm_title = normalize_title(paper.get("title", ""))
        wset = _word_set(paper.get("title", ""))

        # 第一级：DOI 精确匹配
        if doi and doi in doi_map:
            idx = doi_map[doi]
            result[idx] = _merge_papers(result[idx], paper)
            if verbose:
                print(f"[去重] DOI 合并: {paper.get('title', '')[:40]}...", file=sys.stderr)
            continue

        # 第二级：标题归一化精确匹配
        if norm_title and norm_title in title_map:
            idx = title_map[norm_title]
            result[idx] = _merge_papers(result[idx], paper)
            if verbose:
                print(f"[去重] 标题精确合并: {paper.get('title', '')[:40]}...", file=sys.stderr)
            continue

        # 第三级：Jaccard 近重检测
        found_dup = False
        for i, existing_wset in enumerate(word_sets):
            sim = jaccard_similarity(wset, existing_wset)
            if sim >= threshold:
                result[i] = _merge_papers(result[i], paper)
                if verbose:
                    print(f"[去重] 近重合并 (sim={sim:.2f}): {paper.get('title', '')[:40]}...",
                          file=sys.stderr)
                found_dup = True
                break

        if not found_dup:
            # 新文献
            if doi:
                doi_map[doi] = len(result)
            if norm_title:
                title_map[norm_title] = len(result)
            word_sets.append(wset)
            result.append(dict(paper))

    if verbose:
        removed = len(papers) - len(result)
        print(f"[去重] {len(papers)} → {len(result)} 篇（去除 {removed} 篇重复）",
              file=sys.stderr)

    return result


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="跨源文献去重",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从 stdin 读取 JSON 数组去重
  cat results.json | python dedup.py

  # 指定相似度阈值
  cat results.json | python dedup.py --threshold 0.85

  # 从文件读取并保存
  python dedup.py -i results.json -o deduped.json
        """)
    parser.add_argument("--input", "-i", help="输入 JSON 文件（默认从 stdin 读取）")
    parser.add_argument("--output", "-o", help="保存为 JSON 文件（默认输出到 stdout）")
    parser.add_argument("--threshold", "-t", type=float,
                        default=DEDUP_SIMILARITY_THRESHOLD,
                        help=f"Jaccard 相似度阈值 (默认 {DEDUP_SIMILARITY_THRESHOLD})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="打印去重日志")
    args = parser.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            papers = json.load(f)
    else:
        papers = json.load(sys.stdin)

    if not isinstance(papers, list):
        print("[去重] 输入必须是 JSON 数组", file=sys.stderr)
        sys.exit(1)

    result = dedup_papers(papers, threshold=args.threshold, verbose=args.verbose)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[去重] 结果已保存: {args.output} ({len(result)} 条)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
