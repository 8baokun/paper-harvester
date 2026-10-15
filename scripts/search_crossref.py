#!/usr/bin/env python3
"""Crossref 元数据检索（无 OA PDF，但 DOI 全，供后续 Unpaywall 解析）。"""
import sys, json, urllib.parse, urllib.request
from config import UA_API, CROSSREF_API, REQUEST_TIMEOUT, MAX_RESULTS_DEFAULT

def search(query, limit=MAX_RESULTS_DEFAULT, from_year=None, to_year=None):
    params = {"query": query, "rows": str(limit), "select": "title,author,issued,container-title,DOI,is-referenced-by-count,URL"}
    if from_year:
        params["from-pub-date"] = f"{from_year}-01-01"
    if to_year:
        params["until-pub-date"] = f"{to_year}-12-31"

    url = CROSSREF_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA_API})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        data = json.load(r)

    out = []
    for w in data.get("message", {}).get("items", []):
        issued = w.get("issued", {}).get("date-parts", [[""]])[0]
        year = issued[0] if issued else None
        authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in w.get("author", [])][:3]
        out.append({
            "title": (w.get("title") or [""])[0],
            "authors": ", ".join(authors) + (" et al." if len(w.get("author", [])) > 3 else ""),
            "year": year,
            "source": (w.get("container-title") or [""])[0],
            "cited_by_count": w.get("is-referenced-by-count", 0),
            "doi": w.get("DOI"),
            "oa": False,
            "pdf": "",
            "url": w.get("URL"),
        })
    return out

if __name__ == "__main__":
    print(json.dumps(search(sys.argv[1], limit=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_RESULTS_DEFAULT),
                     ensure_ascii=False, indent=2))
