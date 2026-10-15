#!/usr/bin/env python3
"""OpenAlex 元数据检索。无需 key，UA 带邮箱即可。"""
import sys, json, urllib.parse, urllib.request
from datetime import datetime
from config import EMAIL, UA_API, OPENALEX_API, REQUEST_TIMEOUT, MAX_RESULTS_DEFAULT

def search(query, limit=MAX_RESULTS_DEFAULT, from_year=None, to_year=None, open_access=False):
    params = {
        "search": query,
        "per_page": str(limit),
        "select": "id,doi,title,authorships,year,primary_location,cited_by_count,type,publication_year",
    }
    filt = []
    if from_year:
        filt.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filt.append(f"to_publication_date:{to_year}-12-31")
    if open_access:
        filt.append("open_access.is_oa:true")
    if filt:
        params["filter"] = ",".join(filt)

    url = OPENALEX_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA_API})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        data = json.load(r)

    out = []
    for w in data.get("results", []):
        authors = [a["author"]["display_name"] for a in w.get("authorships", [])][:3]
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        out.append({
            "title": w.get("title"),
            "authors": ", ".join(authors) + (" et al." if len(w.get("authorships", [])) > 3 else ""),
            "year": w.get("publication_year") or w.get("year"),
            "source": src.get("display_name"),
            "cited_by_count": w.get("cited_by_count", 0),
            "doi": w.get("doi"),
            "oa": bool(loc.get("is_oa")),
            "pdf": loc.get("pdf_url") or "",
            "url": w.get("id"),
        })
    return out

if __name__ == "__main__":
    q = sys.argv[1]
    print(json.dumps(search(q, limit=int(sys.argv[2]) if len(sys.argv) > 2 else MAX_RESULTS_DEFAULT),
                     ensure_ascii=False, indent=2))
