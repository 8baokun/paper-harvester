#!/usr/bin/env python3
"""arXiv 检索（含 PDF 直链，OA 天然全开）。"""
import sys, json, re
try:
    from feedparser import parse as fp_parse
except ImportError:
    fp_parse = None
import urllib.request, urllib.parse
from config import ARXIV_API, REQUEST_TIMEOUT, MAX_RESULTS_DEFAULT

def search(query, limit=MAX_RESULTS_DEFAULT):
    url = ARXIV_API + "?" + urllib.parse.urlencode({
        "search_query": f"all:{query}", "start": 0, "max_results": str(limit)})
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as r:
        raw = r.read().decode("utf-8", "ignore")
    # 简易解析（无 feedparser 也能跑）
    entries = re.findall(r"<entry>(.*?)</entry>", raw, re.S)
    out = []
    for e in entries:
        def g(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", e, re.S)
            return m.group(1).strip() if m else ""
        title = re.sub(r"\s+", " ", g("title"))
        authors = re.findall(r"<name>(.*?)</name>", e)
        out.append({
            "title": title,
            "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            "year": (g("published") or "")[:4],
            "source": "arXiv",
            "cited_by_count": 0,
            "doi": "",
            "oa": True,
            "pdf": g("id").replace("abs", "pdf"),
            "url": g("id"),
        })
    return out

if __name__ == "__main__":
    print(json.dumps(search(sys.argv[1], limit=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_RESULTS_DEFAULT),
                     ensure_ascii=False, indent=2))
