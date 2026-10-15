#!/usr/bin/env python3
"""Semantic Scholar 元数据 + OA PDF 检索。无需 key（批量用需申请）。"""
import sys, json, urllib.parse, urllib.request
from config import UA_API, S2_API, REQUEST_TIMEOUT, MAX_RESULTS_DEFAULT

def search(query, limit=MAX_RESULTS_DEFAULT, year_start=None, year_end=None):
    params = {
        "query": query,
        "limit": str(limit),
        "fields": "title,authors,year,venue,citationCount,externalIds,openAccessPdf,url",
    }
    url = S2_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA_API})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        data = json.load(r)

    out = []
    for p in data.get("data", []):
        authors = [a["name"] for a in p.get("authors", [])][:3]
        ext = p.get("externalIds") or {}
        oa = p.get("openAccessPdf") or {}
        out.append({
            "title": p.get("title"),
            "authors": ", ".join(authors) + (" et al." if len(p.get("authors", [])) > 3 else ""),
            "year": p.get("year"),
            "source": p.get("venue"),
            "cited_by_count": p.get("citationCount", 0),
            "doi": ext.get("DOI"),
            "oa": bool(oa.get("url")),
            "pdf": oa.get("url") or "",
            "url": p.get("url"),
        })
    return out

if __name__ == "__main__":
    print(json.dumps(search(sys.argv[1], limit=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_RESULTS_DEFAULT),
                     ensure_ascii=False, indent=2))
