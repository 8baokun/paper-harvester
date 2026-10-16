#!/usr/bin/env python3
"""NCPSSD (国家哲学社会科学文献中心) 检索 + 登录 + PDF 下载。

用法:
  # 仅检索（无需登录）
  python search_ncpssd.py "业法财融合" 20

  # 按期刊名检索（返回该期刊发表的文章）
  python search_ncpssd.py --journal "法学研究" 20

  # 检索 + 下载（账号密码从 .env 自动读取）
  python search_ncpssd.py "业法财融合" 20 --download

  # 从已有 JSON 结果下载
  python search_ncpssd.py --stdin --download < results.json

  # 使用 Cookie 文件（避免每次登录）
  python search_ncpssd.py "法学" 10 --download --cookie ncpssd_cookies.json

凭证配置：
  复制 .env.example 为 .env，填入 NCPSSD_USERNAME 和 NCPSSD_PASSWORD。
  .env 不会被 git 提交。

防封号限速：
  NCPSSD 下载间隔默认 30 秒（PH_NCPSSD_DL_GAP 环境变量可调）。

依赖: requests, beautifulsoup4
  pip install requests beautifulsoup4
"""
import sys, os, json, re, time, argparse, signal, random
from pathlib import Path
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] 需要 requests 和 beautifulsoup4: pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

# 从 config.py 引入配置（P2: 配置化基础设施）
from config import (
    NCPSSD_BASE as BASE,
    NCPSSD_LOGIN_URL as LOGIN_URL,
    NCPSSD_SEARCH_API as SEARCH_API,
    NCPSSD_READ_URL as READ_URL,
    NCPSSD_MINIO_SIGN_URL as MINIO_SIGN_URL,
    NCPSSD_JOURNAL_API as JOURNAL_API,
    NCPSSD_USERNAME, NCPSSD_PASSWORD,
    UA, OUT_DIR, COOKIE_FILE, MIN_GAP, NCPSSD_DOWNLOAD_GAP,
    NCPSSD_SEARCH_GAP_MIN, NCPSSD_SEARCH_GAP_MAX,
    MAX_RESULTS_DEFAULT, PAGE_SIZE_MAX,
    REQUEST_TIMEOUT, DOWNLOAD_TIMEOUT, MAX_RETRIES, BACKOFF_FACTOR,
)
from retry import retry_with_backoff, classify_error, should_fallback

# 搜索字段: IKTE=题名, IKPYTE=副题名, IKST=关键词, IKET=作者, IKSE=刊名
SEARCH_FIELDS = ["IKTE", "IKPYTE", "IKST", "IKET", "IKSE"]


def _build_search_condition(query: str) -> str:
    """构造 NCPSSD 搜索条件字符串（不编码，直接传给 API）。

    格式（与 NCPSSD 网站前端 Master.js 一致）:
      (IKTE="关键词" OR IKPYTE="关键词"  OR IKST="关键词" OR IKET="关键词" OR IKSE="关键词")

    注意：外层括号必须有，IKPYTE 后两个空格（与网站 JS 源码完全一致）。
    参考: Master.js L343-344, type="0"（全部文献）的搜索条件构造。
    """
    parts = [
        f'IKTE="{query}"',
        f'IKPYTE="{query}"',
        f'IKST="{query}"',
        f'IKET="{query}"',
        f'IKSE="{query}"',
    ]
    # IKPYTE 后两个空格（与网站 JS 一致），其余 OR 后一个空格
    inner = parts[0] + " OR " + parts[1] + "  OR " + " OR ".join(parts[2:])
    return f"({inner})"


def search(query: str, limit: int = MAX_RESULTS_DEFAULT, session: requests.Session = None,
           journal: str = None) -> list:
    """检索 NCPSSD，返回文献列表。不需要登录。

    Args:
        query: 搜索关键词（多字段 OR 搜索）
        limit: 返回结果数量
        session: 可选的 requests.Session
        journal: 期刊名（指定后用 IKSE=期刊名 精确搜索该期刊文章）
    Returns:
        文献列表 [{"title", "authors", "year", "source", "doi", "oa", "pdf", "url", "lngid", "abstract", "issue"}, ...]
    """
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": BASE + "/"})

    # 先访问首页获取 Cookie（模拟浏览器行为，避免 WAF 拦截）
    try:
        sess.get(BASE, timeout=15)
    except Exception:
        pass

    # 随机等待 20~60 秒（防 WAF 静默拦截）
    gap = random.uniform(NCPSSD_SEARCH_GAP_MIN, NCPSSD_SEARCH_GAP_MAX)
    print(f"[NCPSSD] 等待 {gap:.1f} 秒后搜索（防 WAF 拦截）...", file=sys.stderr)
    time.sleep(gap)

    # 构造搜索条件
    if journal:
        # 按期刊名检索：IKSE=期刊名（无引号，精确匹配）
        condition = f"IKSE={journal}"
        print(f"[NCPSSD] 按期刊检索: {journal}", file=sys.stderr)
    else:
        condition = _build_search_condition(query)

    data = {
        "search": condition,
        "pageNum": "1",
        "pageSize": str(min(limit, 50)),
        "sort": "",
        "sType": "0",
        "ajaxKeys": "",
        "customShowCondition": "",
    }

    try:
        resp = sess.post(SEARCH_API, data=data, timeout=20,
                         headers={"X-Requested-With": "XMLHttpRequest"})
        result = resp.json()
    except Exception as e:
        print(f"[NCPSSD] 搜索 API 请求失败: {e}", file=sys.stderr)
        return []

    if not result.get("result") or not result.get("data"):
        print(f"[NCPSSD] 搜索返回异常: {result}", file=sys.stderr)
        return []

    total = result["data"].get("total", 0)
    rows = result["data"].get("rows", [])
    print(f"[NCPSSD] 检索「{query}」共 {total} 条，返回 {len(rows)} 条", file=sys.stderr)

    papers = []
    for row in rows:
        paper = _parse_article(row)
        if paper:
            papers.append(paper)

    return papers[:limit]


def _parse_article(data: dict) -> dict:
    """解析 NCPSSD API 返回的文章数据。

    NCPSSD 字段映射:
    - id / data_id: 文章唯一标识 (lngid)
    - title: 原始标题 (含 <font> 高亮标签)
    - ik_title: 高亮标题
    - creator: 作者 (含机构编号 如 "何勤华[1]")
    - cbw_name: 期刊名称
    - years: 年份
    - num: 期号
    - remark / ik_remark: 摘要
    - doi: DOI
    - fileName: PDF 文件名
    - pdfurl: PDF 直链 (可能为 null)
    - type: 文献类型
    """
    # 清理标题中的 HTML 高亮标签
    title = data.get("title", "") or ""
    title = re.sub(r"<[^>]+>", "", title).strip()

    # 清理作者: "何勤华[1]" -> "何勤华"
    raw_creator = data.get("creator", "") or ""
    # 去掉 [数字] 和分号分隔
    authors = []
    for part in raw_creator.split(";"):
        part = re.sub(r"\[\d+\]", "", part).strip()
        if part:
            authors.append(part)
    authors_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

    lngid = data.get("id", "") or data.get("data_id", "") or ""
    journal = data.get("cbw_name", "") or ""
    year = str(data.get("years", "") or "")[:4]
    issue = str(data.get("num", "") or "")
    abstract = data.get("remark", "") or data.get("ik_remark", "") or ""
    doi = data.get("doi", "") or ""
    file_name = data.get("fileName", "") or ""
    pdfurl = data.get("pdfurl", "")

    # PDF URL: readurl 端点登录后返回真实 PDF URL
    pdf = f"{READ_URL}?id={lngid}" if lngid else ""

    return {
        "title": title,
        "authors": authors_str,
        "year": year,
        "source": journal,
        "doi": doi,
        "oa": "ncpssd_login",  # 条件OA：NCPSSD 注册后免费下载，非真OA
        "pdf": pdf,
        "url": f"{READ_URL}?id={lngid}" if lngid else "",
        "lngid": lngid,
        "abstract": abstract,
        "issue": issue,
        "file_name": file_name,
        "type": data.get("type", ""),
    }


def login(username: str, password: str, sess: requests.Session = None) -> requests.Session:
    """登录 NCPSSD，返回带 Cookie 的 Session。

    逆向自 NCPSSD 登录页 /login 的 login() JS 函数：
    - 端点：POST /login（不是 /loginHandler/login）
    - 参数：loginType=account, uname=用户名, password=MD5(密码), UrlReferrer=""
    - 成功判断：json.succee == true
    """
    import hashlib
    sess = sess or requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": f"{BASE}/login"})

    # 先访问首页获取初始 Cookie
    try:
        sess.get(BASE, timeout=15)
    except Exception:
        pass

    # 密码 MD5 加密（与前端 $.md5(password) 一致）
    md5_password = hashlib.md5(password.encode("utf-8")).hexdigest()

    login_data = {
        "loginType": "account",
        "uname": username,
        "password": md5_password,
        "UrlReferrer": "",
    }

    resp = sess.post(f"{BASE}/login", data=login_data, timeout=15,
                     headers={"X-Requested-With": "XMLHttpRequest"})
    resp.encoding = "utf-8"

    # 检查登录结果
    try:
        result = resp.json()
        if isinstance(result, dict):
            if result.get("succee"):
                print(f"[NCPSSD] 登录成功: {username}", file=sys.stderr)
                return sess
            else:
                msg = result.get("msg") or f"code={result.get('code')}"
                raise Exception(f"登录失败: {msg}")
    except json.JSONDecodeError:
        if "退出" in resp.text or "logout" in resp.text.lower() or "个人中心" in resp.text:
            print(f"[NCPSSD] 登录成功: {username}", file=sys.stderr)
            return sess
        raise Exception(f"登录失败: 无法解析响应 (HTTP {resp.status_code})")

    return sess


def load_cookies(cookie_path: str, sess: requests.Session = None) -> requests.Session:
    """从文件加载 Cookie。"""
    sess = sess or requests.Session()
    sess.headers.update({"User-Agent": UA})
    if os.path.exists(cookie_path):
        with open(cookie_path, "r") as f:
            cookies = json.load(f)
        for c in cookies:
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        print(f"[NCPSSD] 已加载 Cookie: {cookie_path}", file=sys.stderr)
    return sess


def save_cookies(sess: requests.Session, cookie_path: str):
    """保存 Cookie 到文件。"""
    cookies = []
    for c in sess.cookies:
        cookies.append({"name": c.name, "value": c.value, "domain": c.domain})
    with open(cookie_path, "w") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def _atomic_write(filepath: str, data: bytes):
    """原子写入：先写 .part 文件，成功后 rename。防止中断产生半截文件。"""
    part_path = filepath + ".part"
    try:
        with open(part_path, "wb") as f:
            f.write(data)
        os.rename(part_path, filepath)
    except Exception:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise


def _write_sidecar(filepath: str, paper: dict, source: str = "ncpssd"):
    """写入 JSON sidecar 元数据文件（与 PDF 同名，扩展名 .json）。"""
    sidecar_path = filepath.rsplit(".", 1)[0] + ".json"
    meta = {
        "title": paper.get("title", ""),
        "authors": paper.get("authors", ""),
        "year": paper.get("year", ""),
        "source": paper.get("source", ""),
        "doi": paper.get("doi", ""),
        "oa": paper.get("oa", "ncpssd_login"),
        "download_source": source,
        "download_time": datetime.now().isoformat(),
        "original_url": paper.get("url", ""),
        "original_pdf_url": paper.get("pdf", ""),
        "local_path": os.path.abspath(filepath),
        "file_size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
        "lngid": paper.get("lngid", ""),
        "issue": paper.get("issue", ""),
        "abstract": paper.get("abstract", ""),
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _record_failure(paper: dict, reason: str, error_type: str = ""):
    """记录下载失败到 failed_downloads.txt。"""
    fail_path = os.path.join(OUT_DIR, "failed_downloads.txt")
    try:
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {error_type or 'unknown'}\t{reason}\t{paper.get('title', '')[:60]}\t{paper.get('doi', '')}\n")
    except Exception:
        pass


# ── 优雅中断 ───────────────────────────────────────────
_ncpssd_interrupted = False

def _signal_handler(signum, frame):
    global _ncpssd_interrupted
    _ncpssd_interrupted = True
    print("\n[NCPSSD] 收到中断信号，正在优雅退出...", file=sys.stderr)


def download_pdf(paper: dict, sess: requests.Session, out_dir: str = OUT_DIR) -> dict:
    """下载单篇 NCPSSD 论文 PDF。

    完整下载流程（v0.4 修正，基于 Master.js 逆向）：
    1. GET /Literature/readurl?id={lngid} → 返回 JSON {"url": "https://ft.ncpssd.cn/..."}
    2. POST /common/getMinioSign → 返回签名 {"code": 200, "data": "sign_string"}
    3. GET {pdf_url} with headers: sign, site=npssd, dotype=down → PDF 二进制流

    防封号：下载间隔 30 秒（NCPSSD_DOWNLOAD_GAP）

    Returns: {"status": "downloaded"|"error"|"locked", "path": ..., "note": ..., "source": "ncpssd"}
    """
    lngid = paper.get("lngid", "")
    if not lngid:
        return {"status": "locked", "paper": paper, "note": "无 lngid"}

    # 构造文件名
    year = paper.get("year", "nd") or "nd"
    first_author = (paper.get("authors", "ref") or "ref").split(",")[0].strip()
    safe_name = re.sub(r"[^\w\-.]", "_", first_author)[:30]
    safe_title = re.sub(r"[^\w\-.]", "_", paper.get("title", "")[:30])
    filename = f"{year}_{safe_name}_{safe_title}.pdf"
    filepath = os.path.join(out_dir, filename)

    os.makedirs(out_dir, exist_ok=True)

    # 已存在则跳过
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1024:
        print(f"[NCPSSD] 已存在: {filename}", file=sys.stderr)
        return {"status": "downloaded", "paper": paper, "path": os.path.abspath(filepath),
                "source": "ncpssd"}

    # 防封号限速
    time.sleep(NCPSSD_DOWNLOAD_GAP)

    try:
        # ── Step 1: 获取 PDF URL ──
        article_url = f"{BASE}/Literature/readurl?id={lngid}"
        print(f"[NCPSSD] 获取下载链接: {lngid}", file=sys.stderr)
        resp = sess.get(article_url, timeout=REQUEST_TIMEOUT,
                        headers={"Referer": f"{BASE}/"})
        ct = resp.headers.get("Content-Type", "")

        # 预期返回 JSON {"url": "https://ft.ncpssd.cn/..."}
        pdf_url = None
        if "json" in ct:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    pdf_url = data.get("url", "")
                elif isinstance(data, list) and data:
                    pdf_url = data[0].get("url", "") if isinstance(data[0], dict) else ""
            except Exception:
                pass
        elif resp.content[:5] == b"%PDF-":
            # 直接返回了 PDF
            _atomic_write(filepath, resp.content)
            _write_sidecar(filepath, paper, "ncpssd")
            print(f"[NCPSSD] 下载成功（直链）: {filename} ({len(resp.content)} bytes)", file=sys.stderr)
            return {"status": "downloaded", "paper": paper, "path": os.path.abspath(filepath),
                    "source": "ncpssd"}
        elif "text/html" in ct:
            # 未登录时返回 SPA HTML 页面
            _record_failure(paper, "返回HTML非JSON（可能未登录）", "auth_required")
            return {"status": "locked", "paper": paper,
                    "note": "NCPSSD 需要登录才能下载（返回 SPA HTML）"}

        if not pdf_url:
            _record_failure(paper, f"未获取到PDF URL: ct={ct}", "no_url")
            return {"status": "error", "paper": paper, "note": f"未获取到 PDF URL (ct={ct})"}

        print(f"[NCPSSD] PDF URL: {pdf_url[:80]}...", file=sys.stderr)

        # ── Step 2: 获取 MinioSign ──
        time.sleep(1)
        sign_resp = sess.post(f"{BASE}/common/getMinioSign",
                              headers={"X-Requested-With": "XMLHttpRequest",
                                       "Content-Type": "application/json; charset=utf-8"},
                              timeout=REQUEST_TIMEOUT)
        sign_data = sign_resp.json()
        sign = sign_data.get("data", "")
        if not sign:
            _record_failure(paper, f"获取签名失败: {sign_data}", "sign_failed")
            return {"status": "error", "paper": paper, "note": f"获取签名失败: {sign_data}"}

        # ── Step 3: 用签名下载 PDF ──
        time.sleep(1)
        pdf_resp = sess.get(pdf_url, timeout=DOWNLOAD_TIMEOUT + 30,
                            headers={
                                "sign": sign,
                                "site": "npssd",
                                "dotype": "down",
                                "Referer": article_url,
                            })

        if pdf_resp.content[:5] == b"%PDF-":
            _atomic_write(filepath, pdf_resp.content)
            _write_sidecar(filepath, paper, "ncpssd")
            print(f"[NCPSSD] 下载成功: {filename} ({len(pdf_resp.content)} bytes)", file=sys.stderr)
            return {"status": "downloaded", "paper": paper, "path": os.path.abspath(filepath),
                    "source": "ncpssd"}
        else:
            # 检查是否权限拦截
            try:
                err = pdf_resp.json()
                msg = err.get("msg", "")
                _record_failure(paper, f"PDF下载失败: {msg}", "permission_denied")
                return {"status": "error", "paper": paper,
                        "note": f"PDF 下载被拦截: {msg}"}
            except Exception:
                _record_failure(paper, f"非PDF响应: ct={pdf_resp.headers.get('Content-Type')}", "not_pdf")
                return {"status": "error", "paper": paper,
                        "note": f"非 PDF 响应 (ct={pdf_resp.headers.get('Content-Type')})"}

    except Exception as e:
        error_type = classify_error(e)
        _record_failure(paper, f"下载失败: {e}", error_type)
        return {"status": "error", "paper": paper, "note": f"下载失败: {e}",
                "error_type": error_type, "source": "ncpssd"}


def search_and_download(query: str, limit: int, username: str, password: str,
                        out_dir: str = OUT_DIR, cookie_path: str = None,
                        parallel: bool = False, workers: int = 2) -> dict:
    """检索 + 下载一体化流程。

    改进（v0.3）：
    - 优雅中断（Ctrl+C 保存已完成结果）
    - 并发下载支持（ThreadPoolExecutor）
    """
    # 注册中断信号
    global _ncpssd_interrupted
    _ncpssd_interrupted = False
    signal.signal(signal.SIGINT, _signal_handler)

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    # 登录
    if cookie_path and os.path.exists(cookie_path):
        sess = load_cookies(cookie_path, sess)
        test_resp = sess.get(f"{BASE}/index", timeout=15)
        if "退出" not in test_resp.text and "logout" not in test_resp.text.lower():
            print("[NCPSSD] Cookie 已过期，重新登录...", file=sys.stderr)
            sess = login(username, password, sess)
            if cookie_path:
                save_cookies(sess, cookie_path)
    else:
        sess = login(username, password, sess)
        if cookie_path:
            save_cookies(sess, cookie_path)

    # 检索
    print(f"[NCPSSD] 检索: {query}", file=sys.stderr)
    papers = search(query, limit, sess)
    print(f"[NCPSSD] 检索到 {len(papers)} 条结果", file=sys.stderr)

    if not papers:
        return {"papers": [], "downloaded": [], "failed": [], "stats": {"found": 0, "ok": 0, "fail": 0}}

    # 下载
    downloaded = []
    failed = []
    if parallel and len(papers) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(workers, 4)) as executor:
            futures = {executor.submit(download_pdf, p, sess, out_dir): p for p in papers}
            for future in as_completed(futures):
                if _ncpssd_interrupted:
                    print("[NCPSSD] 中断：跳过剩余任务", file=sys.stderr)
                    break
                try:
                    result = future.result(timeout=120)
                except Exception as e:
                    p = futures[future]
                    result = {"status": "error", "paper": p, "note": str(e)}
                if result["status"] == "downloaded":
                    downloaded.append(result)
                else:
                    failed.append(result)
    else:
        for i, paper in enumerate(papers):
            if _ncpssd_interrupted:
                print(f"[NCPSSD] 中断：已完成 {i}/{len(papers)}", file=sys.stderr)
                break
            print(f"[NCPSSD] 下载 {i+1}/{len(papers)}: {paper.get('title', '')[:50]}...", file=sys.stderr)
            result = download_pdf(paper, sess, out_dir)
            if result["status"] == "downloaded":
                downloaded.append(result)
            else:
                failed.append(result)

    if _ncpssd_interrupted:
        print(f"[NCPSSD] 下载被中断。已下载 {len(downloaded)} 篇，失败 {len(failed)} 篇。", file=sys.stderr)

    return {
        "papers": papers,
        "downloaded": downloaded,
        "failed": failed,
        "stats": {"found": len(papers), "ok": len(downloaded), "fail": len(failed)},
    }


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="NCPSSD 检索与下载")
    parser.add_argument("query", nargs="?", help="搜索关键词")
    parser.add_argument("limit", nargs="?", type=int, default=20, help="结果数量 (默认 20)")
    parser.add_argument("--journal", help="按期刊名检索（如 \"法学研究\"），返回该期刊发表的文章")
    parser.add_argument("--download", action="store_true", help="下载 PDF (需登录)")
    parser.add_argument("--user", default=NCPSSD_USERNAME, help="NCPSSD 用户名 (手机号/邮箱，默认从 .env 读取)")
    parser.add_argument("--pass", dest="password", default=NCPSSD_PASSWORD, help="NCPSSD 密码 (默认从 .env 读取)")
    parser.add_argument("--cookie", default="ncpssd_cookies.json", help="Cookie 文件路径")
    parser.add_argument("--out", default="pdfs", help="PDF 输出目录")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取 JSON 结果进行下载")
    parser.add_argument("--parallel", action="store_true", help="并发下载 (默认串行)")
    parser.add_argument("--workers", type=int, default=2, help="并发数 (默认 2, 最大 4)")
    args = parser.parse_args()

    if args.stdin:
        papers = json.load(sys.stdin)
        if not args.user or not args.password:
            print("[ERROR] 下载需要账号密码。请在 .env 中设置 NCPSSD_USERNAME 和 NCPSSD_PASSWORD", file=sys.stderr)
            print("[ERROR] 或使用 --user 和 --pass 参数传入", file=sys.stderr)
            sys.exit(1)
        sess = requests.Session()
        sess.headers.update({"User-Agent": UA})
        cookie_path = args.cookie
        if os.path.exists(cookie_path):
            sess = load_cookies(cookie_path, sess)
        else:
            sess = login(args.user, args.password, sess)
            save_cookies(sess, cookie_path)

        results = []
        for p in papers:
            if _ncpssd_interrupted:
                print(f"[NCPSSD] 中断：已完成 {len(results)}/{len(papers)}", file=sys.stderr)
                break
            results.append(download_pdf(p, sess, args.out))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not args.query and not args.journal:
        parser.error("需要搜索关键词或 --journal 期刊名")

    # 期刊模式下，query 位置参数如果存在，作为 limit 使用
    journal_limit = args.limit
    if args.journal and args.query:
        try:
            journal_limit = int(args.query)
        except ValueError:
            pass  # 保持默认 limit

    if args.download:
        if not args.user or not args.password:
            print("[ERROR] 下载需要账号密码。请在 .env 中设置 NCPSSD_USERNAME 和 NCPSSD_PASSWORD", file=sys.stderr)
            print("[ERROR] 或使用 --user 和 --pass 参数传入", file=sys.stderr)
            sys.exit(1)
        # 期刊检索模式下的下载
        if args.journal:
            # 先按期刊检索，再下载
            sess = requests.Session()
            sess.headers.update({"User-Agent": UA})
            cookie_path = args.cookie
            if os.path.exists(cookie_path):
                sess = load_cookies(cookie_path, sess)
                test_resp = sess.get(f"{BASE}/index", timeout=15)
                if "退出" not in test_resp.text and "logout" not in test_resp.text.lower():
                    print("[NCPSSD] Cookie 已过期，重新登录...", file=sys.stderr)
                    sess = login(args.user, args.password, sess)
                    save_cookies(sess, cookie_path)
            else:
                sess = login(args.user, args.password, sess)
                save_cookies(sess, cookie_path)
            papers = search("", journal_limit, sess, journal=args.journal)
            downloaded, failed = [], []
            for i, paper in enumerate(papers):
                print(f"[NCPSSD] 下载 {i+1}/{len(papers)}: {paper.get('title', '')[:50]}...", file=sys.stderr)
                r = download_pdf(paper, sess, args.out)
                if r["status"] == "downloaded":
                    downloaded.append(r)
                else:
                    failed.append(r)
            result = {"papers": papers, "downloaded": downloaded, "failed": failed,
                      "stats": {"found": len(papers), "ok": len(downloaded), "fail": len(failed)}}
        else:
            result = search_and_download(args.query, args.limit, args.user, args.password,
                                         args.out, args.cookie,
                                         parallel=args.parallel, workers=args.workers)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.journal:
            papers = search("", journal_limit, journal=args.journal)
        else:
            papers = search(args.query, args.limit)
        print(json.dumps(papers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
