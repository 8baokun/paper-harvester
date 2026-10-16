#!/usr/bin/env python3
"""多源检索结果融合——Reciprocal Rank Fusion (RRF)。

RRF 是一种无监督的多源排序融合方法，不需要各源的原始分数，
只需要各源的排名即可融合。适合 paper-harvester 中各检索源
（NCPSSD / OpenAlex / S2 / Crossref / 微信）分数不可比的情况。

公式: RRF_score(d) = Σ_i 1 / (k + rank_i(d))
  - d: 文档
  - i: 第 i 个检索源
  - rank_i(d): 文档 d 在第 i 个源中的排名（从 1 开始）
  - k: 平滑参数（默认 60，经验值）

用法:
  # 从多个 JSON 文件融合
  python fuse.py ncpssd.json openalex.json s2.json --query "业法财融合"

  # 从 stdin 读取多源 JSON（数组格式）
  cat multi_source.json | python fuse.py --query "自甘风险" --stdin

  # 指定 RRF k 参数
  python fuse.py src1.json src2.json -k 100 --query "国企合规"
"""
import sys, json, re, argparse, os
from typing import List, Dict, Any

from config import RRF_K
from dedup import normalize_title, dedup_papers


def _extract_doi(paper: dict) -> str:
    """提取并归一化 DOI。"""
    doi = paper.get("doi", "") or ""
    return doi.lower().strip().lstrip("https://doi.org/").lstrip("http://doi.org/")


def _paper_key(paper: dict) -> str:
    """生成文献唯一标识：优先 DOI，否则用归一化标题。"""
    doi = _extract_doi(paper)
    if doi:
        return f"doi:{doi}"
    title = normalize_title(paper.get("title", ""))
    if title:
        return f"title:{title}"
    # 最后兜底：原始标题
    return f"raw:{paper.get('title', '')}"


def rrf_fuse(source_results: Dict[str, List[dict]],
             k: int = RRF_K,
             query: str = "") -> List[dict]:
    """RRF 多源融合。

    Args:
        source_results: {源名: [paper, ...], ...}
            每个源的 paper 列表已按该源自身的相关性排好序。
        k: RRF 平滑参数（默认 60）。k 越大，排名差异的影响越小。
        query: 检索关键词（仅用于日志输出）。

    Returns:
        融合去重后的 paper 列表，按 RRF 分数降序排列。
        每篇 paper 增加 rrf_score 和 rrf_sources 字段。
    """
    # 为每篇文献计算 RRF 分数
    # key → {"paper": paper, "score": float, "sources": set()}
    fused: Dict[str, dict] = {}

    for source_name, papers in source_results.items():
        if not papers:
            continue
        for rank, paper in enumerate(papers, start=1):
            key = _paper_key(paper)
            rrf_delta = 1.0 / (k + rank)

            if key not in fused:
                fused[key] = {
                    "paper": paper,
                    "score": 0.0,
                    "sources": set(),
                    "ranks": {},
                }
            fused[key]["score"] += rrf_delta
            fused[key]["sources"].add(source_name)
            fused[key]["ranks"][source_name] = rank

    # 按分数降序排列
    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)

    # 构造输出
    result = []
    for item in ranked:
        paper = item["paper"]
        paper["rrf_score"] = round(item["score"], 6)
        paper["rrf_sources"] = sorted(item["sources"])
        paper["rrf_ranks"] = item["ranks"]
        # 多源命中标记
        if len(item["sources"]) > 1:
            paper["multi_source"] = True
            paper["source_count"] = len(item["sources"])
        result.append(paper)

    if query:
        total = len(result)
        multi = sum(1 for r in result if r.get("multi_source"))
        print(f"[RRF] 「{query}」融合后 {total} 篇（{multi} 篇多源命中，k={k}）",
              file=sys.stderr)

    return result


def fuse_from_files(filepaths: List[str], k: int = RRF_K,
                    query: str = "") -> List[dict]:
    """从多个 JSON 文件读取各源结果并融合。

    文件名作为源名标识：
      ncpssd_results.json → "ncpssd"
      openalex_results.json → "openalex"
      s2_results.json → "s2"
    """
    source_results = {}
    for fp in filepaths:
        if not os.path.exists(fp):
            print(f"[RRF] 文件不存在: {fp}", file=sys.stderr)
            continue
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"[RRF] {fp} 不是数组格式，跳过", file=sys.stderr)
            continue
        # 从文件名提取源名
        basename = os.path.basename(fp).lower()
        if "ncpssd" in basename:
            src = "ncpssd"
        elif "openalex" in basename:
            src = "openalex"
        elif "s2" in basename or "semantic" in basename:
            src = "s2"
        elif "crossref" in basename:
            src = "crossref"
        elif "wechat" in basename or "sogou" in basename:
            src = "wechat"
        elif "arxiv" in basename:
            src = "arxiv"
        else:
            src = basename.replace(".json", "").replace("_results", "")
        source_results[src] = data
        print(f"[RRF] 加载 {src}: {len(data)} 条 ({fp})", file=sys.stderr)

    return rrf_fuse(source_results, k=k, query=query)


def fuse_from_json(data: dict, k: int = RRF_K,
                   query: str = "") -> List[dict]:
    """从多源 JSON 字典融合。

    输入格式: {"ncpssd": [...], "openalex": [...], ...}
    """
    return rrf_fuse(data, k=k, query=query)


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="RRF 多源检索结果融合",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从多个 JSON 文件融合
  python fuse.py ncpssd.json openalex.json s2.json --query "业法财融合"

  # 从 stdin 读取多源 JSON 字典
  cat multi_source.json | python fuse.py --query "自甘风险" --stdin

  # 指定 k 参数
  python fuse.py src1.json src2.json -k 100 --query "国企合规"
        """)
    parser.add_argument("files", nargs="*", help="各源 JSON 文件路径")
    parser.add_argument("--query", "-q", default="", help="检索关键词（用于日志）")
    parser.add_argument("-k", type=int, default=RRF_K,
                        help=f"RRF 平滑参数 (默认 {RRF_K})")
    parser.add_argument("--stdin", action="store_true",
                        help="从 stdin 读取多源 JSON 字典")
    parser.add_argument("--output", "-o", help="保存为 JSON 文件")
    parser.add_argument("--limit", "-l", type=int, default=0,
                        help="返回数量 (0=全部)")
    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            results = fuse_from_json(data, k=args.k, query=args.query)
        elif isinstance(data, list) and data and isinstance(data[0], list):
            # 数组的数组：按顺序命名
            names = [f"source_{i}" for i in range(len(data))]
            results = rrf_fuse(dict(zip(names, data)), k=args.k, query=args.query)
        else:
            print("[RRF] stdin 格式不支持：需要字典或数组的数组", file=sys.stderr)
            sys.exit(1)
    elif args.files:
        results = fuse_from_files(args.files, k=args.k, query=args.query)
    else:
        parser.error("需要文件参数或 --stdin")

    # 去重（RRF 后可能仍有近重）
    results = dedup_papers(results)

    if args.limit > 0:
        results = results[:args.limit]

    output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[RRF] 结果已保存: {args.output} ({len(results)} 条)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
