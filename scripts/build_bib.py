#!/usr/bin/env python3
"""从文献条目生成 BibTeX，并在下载后附加本地 pdf 路径注释。"""
import sys, json, re

def _safe(s):
    return re.sub(r"[^\w\-.]", "_", str(s))[:40]

def to_bibtex(item, pdf_path=None):
    first = (item.get("authors") or "ref").split(",")[0]
    year = item.get("year") or "nd"
    key = f"{_safe(first).lower()}{year}"
    lines = [
        f"@article{{{key},",
        f"  title  = {{{item.get('title','')}}}",
        f"  author = {{{item.get('authors','')}}}",
        f"  year   = {{{year}}}",
        f"  journal= {{{item.get('source','')}}}",
    ]
    if item.get("doi"):
        lines.append(f"  doi    = {{{item['doi']}}}")
    if item.get("url"):
        lines.append(f"  url    = {{{item['url']}}}")
    if pdf_path:
        lines.append(f"  file   = {{file://{pdf_path}}}")
    lines.append("}")
    return "\n".join(lines)

if __name__ == "__main__":
    payload = json.load(sys.stdin)
    # payload 可以是 papers 列表，或 download_oa.run 返回的 result dict
    if isinstance(payload, dict) and "downloaded" in payload:
        by_id = {id(d["paper"]): d["path"] for d in payload["downloaded"]}
        papers = payload.get("all") or list({id(d["paper"]): d["paper"] for d in payload["downloaded"]}.values())
        out = []
        for p in papers:
            out.append(to_bibtex(p, by_id.get(id(p))))
        print("\n\n".join(out))
    else:
        print("\n\n".join(to_bibtex(i) for i in payload))
