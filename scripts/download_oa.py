#!/usr/bin/env python3
"""OA 论文自动下载器（v0.4 — 8级降级链 + PDF深度验证 + 原子写入）。

改进（v0.4）：
- 8级多源降级链：原始URL → Unpaywall → Crossref → OpenAlex → Semantic Scholar → SSRN → Europe PMC → arXiv → locked
- 新增 Semantic Scholar 源（有 openAccessPdf 字段，法学/合规类英文论文覆盖较好）
- 新增 SSRN 源（法学预印本主阵地）
- PDF深度验证：%PDF-签名 + 前两页文本与标题匹配（防HTML伪装PDF）
- 原子写入：.part临时文件 → rename（防中断产生半截文件）
- 优雅中断：捕获Ctrl+C，保存已完成结果
- 失败分类记录：failed_downloads.txt
- 并发下载支持（ThreadPoolExecutor + 限速器）
- JSON Sidecar 元数据
"""
import sys, json, os, time, re, hashlib, signal
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import (
    UA, UA_API, EMAIL, OUT_DIR, MIN_GAP, MAX_PER_MIN,
    REQUEST_TIMEOUT, DOWNLOAD_TIMEOUT, MAX_RETRIES, BACKOFF_FACTOR,
    UNPAYWALL_API, OPENALEX_API, CROSSREF_API, ARXIV_API,
    S2_API, S2_API_KEY, SSRN_SEARCH_URL, SSRN_PDF_BASE,
)
from retry import retry_with_backoff, classify_error, should_retry, should_fallback

# ── 限速器 ─────────────────────────────────────────────
_last_requests = []

def _ratelimit():
    global _last_requests
    now = time.time()
    _last_requests = [t for t in _last_requests if now - t < 60]
    if len(_last_requests) >= MAX_PER_MIN:
        sleep = 60 - (now - _last_requests[0]) + 0.5
        time.sleep(sleep)
        now = time.time()
        _last_requests = [t for t in _last_requests if now - t < 60]
    _last_requests.append(time.time())
    time.sleep(MIN_GAP)


# ── 工具函数 ───────────────────────────────────────────
def _safe(s):
    return re.sub(r"[^\w\-.]", "_", str(s))[:40]

def _path_of(paper, suffix=""):
    year = paper.get("year") or "nd"
    first = (paper.get("authors") or "ref").split(",")[0]
    doi = (paper.get("doi") or "").replace("/", "_").replace(":", "_")[-8:]
    name = f"{year}_{_safe(first)}_{doi}"
    if suffix:
        name += f"_{suffix}"
    return os.path.join(OUT_DIR, f"{name}.pdf")

def _doi_url(doi):
    return f"https://doi.org/{urllib.parse.quote(doi)}" if doi else ""

def _is_pdf(data: bytes) -> bool:
    """校验是否为 PDF 文件（%PDF- 签名）。"""
    return len(data) > 1024 and data[:5] == b"%PDF-"

def _verify_pdf_title(data: bytes, expected_title: str, min_match: float = 0.3) -> bool:
    """深度验证 PDF：提取前两页文本，与标题做关键词匹配。

    防止 HTML 页面被伪装为 PDF 或下载到错误文件。
    匹配策略：标题分词后，有多少词出现在 PDF 前两页文本中。
    """
    if not _is_pdf(data):
        return False
    try:
        import io
        # 尝试用 PyMuPDF 提取文本
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=data, filetype="pdf")
            text = ""
            for i in range(min(2, len(doc))):
                text += doc[i].get_text()
            doc.close()
        except ImportError:
            # PyMuPDF 不可用时，用简单的二进制文本提取
            # 提取 PDF 中的文本流（粗略）
            text = data.decode("latin-1", errors="ignore")
            # 提取括号内的文本
            texts = re.findall(r"\(([^)]{2,100})\)", text)
            text = " ".join(texts[:200])

        if not text or not expected_title:
            return True  # 无法验证时放行

        # 标题分词（中文按字，英文按词）
        title_clean = re.sub(r"[^\w\u4e00-\u9fff]", " ", expected_title.lower())
        words = [w for w in title_clean.split() if len(w) >= 2]
        if not words:
            return True

        text_lower = text.lower()
        matched = sum(1 for w in words if w in text_lower)
        ratio = matched / len(words) if words else 0
        return ratio >= min_match
    except Exception:
        # 验证失败时放行（不阻塞下载流程）
        return True


def _download_url(url: str, headers: dict = None) -> bytes:
    """带重试的 URL 下载，返回文件内容。"""
    @retry_with_backoff(max_retries=MAX_RETRIES)
    def _fetch():
        hdrs = {"User-Agent": UA, "Accept": "*/*"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
            return r.read()
    return _fetch()


def _atomic_write(filepath: str, data: bytes):
    """原子写入：先写 .part 文件，成功后 rename。"""
    part_path = filepath + ".part"
    try:
        with open(part_path, "wb") as f:
            f.write(data)
        os.rename(part_path, filepath)
    except Exception:
        # 清理 .part 文件
        if os.path.exists(part_path):
            os.remove(part_path)
        raise


# ── 降级链各源 ─────────────────────────────────────────

def _try_original_url(pdf_url: str, title: str) -> tuple:
    """源1：原始 PDF URL。Returns: (data, True) or (None, should_fallback)"""
    if not pdf_url:
        return None, True
    try:
        _ratelimit()
        data = _download_url(pdf_url)
        if _is_pdf(data):
            if _verify_pdf_title(data, title):
                return data, True
            else:
                print(f"  [OA] 原始URL PDF标题不匹配，降级...", file=sys.stderr)
                return None, True
        else:
            print(f"  [OA] 原始URL返回非PDF，降级...", file=sys.stderr)
            return None, True
    except Exception as e:
        error_type = classify_error(e)
        print(f"  [OA] 原始URL失败 ({error_type})，降级...", file=sys.stderr)
        return None, should_fallback(error_type)


def _try_unpaywall(doi: str, title: str) -> tuple:
    """源2：Unpaywall API。Returns: (data, True) or (None, should_fallback)"""
    if not doi:
        return None, True
    try:
        url = UNPAYWALL_API.format(doi=urllib.parse.quote(doi))
        params = urllib.parse.urlencode({"email": EMAIL})
        full_url = f"{url}?{params}"
        req = urllib.request.Request(full_url, headers={"User-Agent": UA_API})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data = json.loads(r.read())
        best = data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf") or best.get("url") or ""
        if not pdf_url:
            return None, True
        print(f"  [Unpaywall] 找到 OA PDF: {pdf_url[:80]}", file=sys.stderr)
        _ratelimit()
        pdf_data = _download_url(pdf_url)
        if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
            return pdf_data, True
        else:
            print(f"  [Unpaywall] PDF验证失败，降级...", file=sys.stderr)
            return None, True
    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [Unpaywall] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_crossref(doi: str, title: str) -> tuple:
    """源3：Crossref API（查链接和全文URL）。Returns: (data, True) or (None, should_fallback)"""
    if not doi:
        return None, True
    try:
        url = f"{CROSSREF_API}/{urllib.parse.quote(doi)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA_API})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data = json.loads(r.read())
        work = data.get("message", {})
        # 查找全文链接
        links = work.get("link", [])
        for link in links:
            url_type = link.get("content-type", "")
            if "pdf" in url_type.lower() or "pdf" in link.get("URL", "").lower():
                pdf_url = link["URL"]
                print(f"  [Crossref] 找到 PDF 链接: {pdf_url[:80]}", file=sys.stderr)
                _ratelimit()
                pdf_data = _download_url(pdf_url)
                if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
                    return pdf_data, True
        return None, True
    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [Crossref] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_openalex(doi: str, title: str) -> tuple:
    """源4：OpenAlex API（查OA PDF URL）。Returns: (data, True) or (None, should_fallback)"""
    if not doi:
        return None, True
    try:
        # OpenAlex 用 DOI 查询
        doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi_clean)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA_API})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data = json.loads(r.read())
        # 查找 OA PDF URL
        best_oa = data.get("best_oa_location") or {}
        pdf_url = best_oa.get("pdf_url") or ""
        if not pdf_url:
            # 查找所有 OA 位置
            for loc in data.get("locations", []):
                if loc.get("pdf_url"):
                    pdf_url = loc["pdf_url"]
                    break
        if pdf_url:
            print(f"  [OpenAlex] 找到 OA PDF: {pdf_url[:80]}", file=sys.stderr)
            _ratelimit()
            pdf_data = _download_url(pdf_url)
            if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
                return pdf_data, True
            else:
                print(f"  [OpenAlex] PDF验证失败，降级...", file=sys.stderr)
        return None, True
    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [OpenAlex] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_europe_pmc(doi: str, title: str) -> tuple:
    """源5：Europe PMC API（生物医学领域OA全文）。Returns: (data, True) or (None, should_fallback)"""
    if not doi:
        return None, True
    try:
        # Europe PMC REST API
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{urllib.parse.quote(doi)}&format=json&resultType=core"
        req = urllib.request.Request(url, headers={"User-Agent": UA_API})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data = json.loads(r.read())
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None, True
        result = results[0]
        # 检查是否有全文 URL
        pmcid = result.get("pmcid", "")
        if pmcid:
            pdf_url = f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"
            print(f"  [EuropePMC] 找到 PMC 全文: {pmcid}", file=sys.stderr)
            _ratelimit()
            pdf_data = _download_url(pdf_url)
            if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
                return pdf_data, True
        return None, True
    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [EuropePMC] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_arxiv(doi: str, title: str, paper: dict) -> tuple:
    """源6：arXiv（预印本）。Returns: (data, True) or (None, should_fallback)"""
    try:
        # 如果 paper 有 arxiv id
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id and doi:
            # 从 DOI 中提取 arxiv id
            if "arxiv" in doi.lower():
                arxiv_id = doi.split(":")[-1] if ":" in doi else doi.split("/")[-1]
        if not arxiv_id:
            # 用标题搜索 arXiv
            search_url = ARXIV_API + "?" + urllib.parse.urlencode({
                "search_query": f"ti:{urllib.parse.quote(title[:100])}",
                "max_results": "1"
            })
            req = urllib.request.Request(search_url, headers={"User-Agent": UA_API})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                raw = r.read().decode("utf-8", "ignore")
            # 简单解析 Atom feed
            id_match = re.search(r"<id>http://arxiv\.org/abs/([^<]+)</id>", raw)
            if id_match:
                arxiv_id = id_match.group(1)
        if arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            print(f"  [arXiv] 尝试下载: {arxiv_id}", file=sys.stderr)
            _ratelimit()
            pdf_data = _download_url(pdf_url)
            if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
                return pdf_data, True
        return None, True
    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [arXiv] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_semantic_scholar(doi: str, title: str, paper: dict) -> tuple:
    """源7：Semantic Scholar API（有 openAccessPdf 字段，法学/合规类覆盖较好）。

    实测：搜索"state-owned enterprise compliance"返回 12856 条，3篇有 OA PDF。
    无 API key 时限流严格（429），需要指数退避。
    Returns: (data, True) or (None, should_fallback)
    """
    try:
        # 构建查询参数
        fields = "title,openAccessPdf,externalIds,year,authors"
        params = {
            "query": title[:200],
            "limit": "5",
            "fields": fields,
        }
        if S2_API_KEY:
            params["api_key"] = S2_API_KEY

        search_url = S2_API + "?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": UA_API}
        if S2_API_KEY:
            headers["x-api-key"] = S2_API_KEY

        # Semantic Scholar 限流严格，加额外退避（最多重试2次，避免阻塞太久）
        s2_data = None
        for attempt in range(2):  # 最多重试2次
            try:
                req = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    s2_data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = BACKOFF_FACTOR * (2 ** (attempt + 2))  # 2s, 4s
                    print(f"  [S2] 限流 429，等待 {wait:.1f}s 重试 ({attempt+1}/2)", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise
            except Exception as e:
                error_type = classify_error(e)
                if error_type == "timeout":
                    print(f"  [S2] 超时，降级...", file=sys.stderr)
                    return None, True
                raise

        if s2_data is None:
            print(f"  [S2] 持续限流，降级...", file=sys.stderr)
            return None, True

        data = s2_data

        papers_list = data.get("data", [])
        if not papers_list:
            return None, True

        # 找最匹配的论文（标题相似度）
        best_paper = None
        best_score = 0
        title_lower = title.lower()
        for p in papers_list:
            p_title = (p.get("title") or "").lower()
            # 简单的标题匹配度
            common = sum(1 for w in title_lower.split() if w in p_title)
            score = common / max(len(title_lower.split()), 1)
            if score > best_score:
                best_score = score
                best_paper = p

        if not best_paper or best_score < 0.3:
            # 也尝试用 DOI 精确查询
            if doi:
                doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
                doi_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi_clean)}?fields=openAccessPdf,title"
                try:
                    req2 = urllib.request.Request(doi_url, headers=headers)
                    with urllib.request.urlopen(req2, timeout=REQUEST_TIMEOUT) as r2:
                        doi_data = json.loads(r2.read())
                    if doi_data.get("openAccessPdf"):
                        pdf_url = doi_data["openAccessPdf"].get("url", "")
                        if pdf_url:
                            best_paper = doi_data
                except Exception:
                    pass

        if not best_paper:
            return None, True

        oa_pdf = best_paper.get("openAccessPdf")
        if not oa_pdf or not oa_pdf.get("url"):
            return None, True

        pdf_url = oa_pdf["url"]
        print(f"  [S2] 找到 OA PDF: {pdf_url[:80]}", file=sys.stderr)
        _ratelimit()
        pdf_data = _download_url(pdf_url)
        if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title):
            return pdf_data, True
        else:
            print(f"  [S2] PDF验证失败，降级...", file=sys.stderr)
            return None, True

    except Exception as e:
        error_type = classify_error(e)
        if error_type != "not_found":
            print(f"  [S2] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


def _try_ssrn(doi: str, title: str, paper: dict) -> tuple:
    """源8：SSRN（法学预印本主阵地）。

    SSRN 是 Social Science Research Network，法学预印本的主要发布平台。
    通过搜索页面获取 abstract ID，构造 PDF 下载 URL。
    注意：SSRN 在内网可能不可达（超时），外网环境可用。

    Returns: (data, True) or (None, should_fallback)
    """
    try:
        # SSRN 搜索页面
        params = urllib.parse.urlencode({
            "txtKey_Words": title[:200],
            "npage": "1",
        })
        search_url = f"{SSRN_SEARCH_URL}?{params}"
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

        _ratelimit()
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT + 10) as r:
            html = r.read().decode("utf-8", errors="ignore")

        # 从搜索结果页面提取 abstract ID
        # SSRN 搜索结果中的链接格式: /sol3/papers.cfm?abstract_id=XXXXXXX
        abstract_ids = re.findall(r"abstract_id=(\d+)", html)
        if not abstract_ids:
            # 也尝试另一种格式
            abstract_ids = re.findall(r"abstractId=(\d+)", html)

        if not abstract_ids:
            return None, True

        # 去重，取前3个尝试
        seen = set()
        unique_ids = []
        for aid in abstract_ids:
            if aid not in seen:
                seen.add(aid)
                unique_ids.append(aid)
        unique_ids = unique_ids[:3]

        for abstract_id in unique_ids:
            # SSRN PDF URL 格式
            pdf_url = f"{SSRN_PDF_BASE}/SSRN_ID{abstract_id}.pdf?abstractid={abstract_id}"
            print(f"  [SSRN] 尝试下载 abstract_id={abstract_id}", file=sys.stderr)
            _ratelimit()
            try:
                pdf_data = _download_url(pdf_url, headers={
                    "Referer": f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={abstract_id}",
                })
                if _is_pdf(pdf_data) and _verify_pdf_title(pdf_data, title, min_match=0.2):
                    return pdf_data, True
            except Exception as e:
                error_type = classify_error(e)
                if error_type == "timeout":
                    print(f"  [SSRN] 超时（内网可能不可达），降级...", file=sys.stderr)
                    return None, True  # 内网超时直接跳过
                continue

        return None, True

    except Exception as e:
        error_type = classify_error(e)
        if error_type == "timeout":
            print(f"  [SSRN] 超时（内网可能不可达），降级...", file=sys.stderr)
        elif error_type != "not_found":
            print(f"  [SSRN] 查询失败 ({error_type})", file=sys.stderr)
        return None, True


# ── JSON Sidecar ───────────────────────────────────────
def _write_sidecar(filepath: str, paper: dict, source: str):
    """写入 JSON sidecar 元数据文件。"""
    sidecar_path = filepath.replace(".pdf", ".json")
    meta = {
        "title": paper.get("title", ""),
        "authors": paper.get("authors", ""),
        "year": paper.get("year", ""),
        "source": paper.get("source", ""),
        "doi": paper.get("doi", ""),
        "oa": paper.get("oa", False),
        "download_source": source,
        "download_time": datetime.now().isoformat(),
        "original_url": paper.get("url", ""),
        "original_pdf_url": paper.get("pdf", ""),
        "local_path": os.path.abspath(filepath),
        "file_size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 失败记录 ───────────────────────────────────────────
def _record_failure(paper: dict, reason: str, error_type: str = ""):
    """记录失败到 failed_downloads.txt。"""
    fail_path = os.path.join(OUT_DIR, "failed_downloads.txt")
    try:
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {error_type or 'unknown'}\t{reason}\t{paper.get('title', '')[:60]}\t{paper.get('doi', '')}\n")
    except Exception:
        pass


# ── 主下载函数 ─────────────────────────────────────────
def download(paper):
    """下载单篇论文——8级多源降级链。

    降级顺序：
    1. 原始 PDF URL（如有 oa=True 且有 pdf）
    2. Unpaywall 查找（如有 DOI）
    3. Crossref 查找全文链接（如有 DOI）
    4. OpenAlex 查找 OA PDF（如有 DOI）
    5. Semantic Scholar 查找 OA PDF（按标题搜索或 DOI 精确查询）
    6. SSRN 法学预印本（按标题搜索）
    7. Europe PMC 查找全文（如有 DOI）
    8. arXiv 预印本（标题搜索或 DOI 含 arxiv）
    9. 标记 locked

    Returns: {"status": "downloaded"|"error"|"locked"|"not-pdf", "paper": ..., "path": ..., "note": ..., "source": ...}
    """
    oa = paper.get("oa", False)
    doi = paper.get("doi", "")
    pdf_url = paper.get("pdf", "")
    title = paper.get("title", "")

    # 非OA且无条件OA标记 → locked
    if oa is False or (oa == "" and not pdf_url):
        _record_failure(paper, "非OA，需机构权限", "locked")
        return {"status": "locked", "paper": paper,
                "note": f"需机构权限：{_doi_url(doi)}\n   建议：学校 WebVPN / CARSI / 图书馆文献传递"}

    # ncpssd_login 类型不在 download_oa 处理
    if oa == "ncpssd_login":
        return {"status": "locked", "paper": paper,
                "note": f"NCPSSD 条件OA，需登录后下载（使用 search_ncpssd.py --download）"}

    os.makedirs(OUT_DIR, exist_ok=True)

    # ── 源1：原始 PDF URL ──
    if pdf_url:
        data, do_fallback = _try_original_url(pdf_url, title)
        if data:
            path = _path_of(paper)
            _atomic_write(path, data)
            _write_sidecar(path, paper, "original")
            return {"status": "downloaded", "paper": paper,
                    "path": os.path.abspath(path), "source": "original"}
        if not do_fallback:
            _record_failure(paper, "原始URL失败且不可降级", "fatal")
            return {"status": "error", "paper": paper, "note": "原始URL失败且不可降级"}

    # ── 源2：Unpaywall ──
    if doi:
        data, do_fallback = _try_unpaywall(doi, title)
        if data:
            path = _path_of(paper, suffix="upw")
            _atomic_write(path, data)
            _write_sidecar(path, paper, "unpaywall")
            return {"status": "downloaded", "paper": paper,
                    "path": os.path.abspath(path), "source": "unpaywall"}

    # ── 源3：Crossref ──
    if doi:
        data, do_fallback = _try_crossref(doi, title)
        if data:
            path = _path_of(paper, suffix="cr")
            _atomic_write(path, data)
            _write_sidecar(path, paper, "crossref")
            return {"status": "downloaded", "paper": paper,
                    "path": os.path.abspath(path), "source": "crossref"}

    # ── 源4：OpenAlex ──
    if doi:
        data, do_fallback = _try_openalex(doi, title)
        if data:
            path = _path_of(paper, suffix="oa")
            _atomic_write(path, data)
            _write_sidecar(path, paper, "openalex")
            return {"status": "downloaded", "paper": paper,
                    "path": os.path.abspath(path), "source": "openalex"}

    # ── 源5：Semantic Scholar ──
    data, do_fallback = _try_semantic_scholar(doi, title, paper)
    if data:
        path = _path_of(paper, suffix="s2")
        _atomic_write(path, data)
        _write_sidecar(path, paper, "semantic_scholar")
        return {"status": "downloaded", "paper": paper,
                "path": os.path.abspath(path), "source": "semantic_scholar"}

    # ── 源6：SSRN（法学预印本）──
    data, do_fallback = _try_ssrn(doi, title, paper)
    if data:
        path = _path_of(paper, suffix="ssrn")
        _atomic_write(path, data)
        _write_sidecar(path, paper, "ssrn")
        return {"status": "downloaded", "paper": paper,
                "path": os.path.abspath(path), "source": "ssrn"}

    # ── 源7：Europe PMC ──
    if doi:
        data, do_fallback = _try_europe_pmc(doi, title)
        if data:
            path = _path_of(paper, suffix="pmc")
            _atomic_write(path, data)
            _write_sidecar(path, paper, "europepmc")
            return {"status": "downloaded", "paper": paper,
                    "path": os.path.abspath(path), "source": "europepmc"}

    # ── 源8：arXiv ──
    data, do_fallback = _try_arxiv(doi, title, paper)
    if data:
        path = _path_of(paper, suffix="arx")
        _atomic_write(path, data)
        _write_sidecar(path, paper, "arxiv")
        return {"status": "downloaded", "paper": paper,
                "path": os.path.abspath(path), "source": "arxiv"}

    # ── 全部失败 ──
    _record_failure(paper, "所有源均不可用", "exhausted")
    return {"status": "locked", "paper": paper,
            "note": f"8级降级链全部不可用。DOI: {_doi_url(doi)}\n   建议：学校 WebVPN / CARSI / 图书馆文献传递"}


# ── 批量下载（支持并发 + 优雅中断）────────────────────
_interrupted = False

def _signal_handler(signum, frame):
    global _interrupted
    _interrupted = True
    print("\n[OA] 收到中断信号，正在优雅退出...", file=sys.stderr)

def run(papers, parallel=False, workers=2):
    """对 papers 中可下载条目执行下载，返回统计与明细。

    Args:
        papers: 文献列表
        parallel: 是否并发下载
        workers: 并发数（parallel=True 时生效）
    """
    # 注册中断信号
    signal.signal(signal.SIGINT, _signal_handler)

    q = [p for p in papers if (p.get("oa") is True or (p.get("pdf") and p.get("oa") is not False))]
    print(f"[OA 下载] 候选 {len(q)} 篇，总检索 {len(papers)} 篇", file=sys.stderr)
    if not q:
        return {"downloaded": [], "locked": papers, "failed": [],
                "stats": {"ok": 0, "locked": len(papers), "fail": 0}}

    results = []
    if parallel and len(q) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, 4)) as ex:
            futures = {ex.submit(download, p): p for p in q}
            for future in as_completed(futures):
                if _interrupted:
                    print("[OA] 中断：跳过剩余任务", file=sys.stderr)
                    break
                try:
                    results.append(future.result(timeout=120))
                except Exception as e:
                    p = futures[future]
                    results.append({"status": "error", "paper": p, "note": str(e)})
    else:
        for i, p in enumerate(q):
            if _interrupted:
                print(f"[OA] 中断：已完成 {i}/{len(q)}", file=sys.stderr)
                break
            results.append(download(p))

    ok = [r for r in results if r["status"] == "downloaded"]
    fail = [r for r in results if r["status"] not in ("downloaded",)]
    for r in ok:
        print(f"  [OA] {r.get('source', '?')}: {r['path']}", file=sys.stderr)

    oa_done = {id(r["paper"]) for r in results}
    still_locked = [p for p in papers if id(p) not in oa_done]

    for r in fail:
        if r.get("note"):
            print(f"  [OA] {r['status']}: {r['note'][:80]}", file=sys.stderr)

    if _interrupted:
        print(f"[OA] 下载被中断。已下载 {len(ok)} 篇，失败 {len(fail)} 篇。", file=sys.stderr)

    return {
        "downloaded": [{"paper": r["paper"], "path": r["path"],
                        "source": r.get("source", "")} for r in ok],
        "locked": still_locked,
        "failed": fail,
        "stats": {"ok": len(ok), "locked": len(still_locked), "fail": len(fail),
                   "interrupted": _interrupted},
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OA 论文下载器（8级降级链）")
    parser.add_argument("--parallel", action="store_true", help="并发下载")
    parser.add_argument("--workers", type=int, default=2, help="并发数 (默认 2)")
    args = parser.parse_args()

    papers = json.load(sys.stdin)
    res = run(papers, parallel=args.parallel, workers=args.workers)
    print(json.dumps(res, ensure_ascii=False, indent=2))
