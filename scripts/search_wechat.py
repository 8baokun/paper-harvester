#!/usr/bin/env python3
"""搜狗微信公众号文章搜索。

通过搜狗微信搜索（weixin.sogou.com）搜索微信公众号文章，
返回标题、摘要、公众号名称、发布时间等元数据。

限制说明：
  - 搜索结果页可正常获取（标题/摘要/公众号名称）
  - 搜狗返回的文章链接为 /link?url=... 跳转链接，跟进会被反爬拦截
  - 因此本脚本只做"搜索 + 元数据收集"，不做全文下载
  - 如需全文，可用 download_wechat.py 配合手动获取的微信 URL

用法:
  # 基本搜索
  python search_wechat.py "自甘风险 民法典1176"

  # 指定结果数量
  python search_wechat.py "业法财融合" --limit 20

  # 搜索并保存为 JSON 文件
  python search_wechat.py "重大过失 体育侵权" -o results.json

  # 搜索多个关键词并合并结果
  python search_wechat.py "自甘风险 体育" "重大过失 竞技体育" --limit 10

依赖: requests, beautifulsoup4
  pip install requests beautifulsoup4
"""
import sys, os, json, re, time, argparse
from urllib.parse import quote, urljoin
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] 需要 requests 和 beautifulsoup4: pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

from config import UA, REQUEST_TIMEOUT, MAX_RETRIES, BACKOFF_FACTOR
from retry import retry_with_backoff, classify_error

# 搜狗微信搜索 URL
SOGOU_WEIXIN_URL = "https://weixin.sogou.com/weixin"

# 搜索间隔（秒），避免被搜狗反爬
SEARCH_GAP = 3.0


def _build_search_url(query: str, page: int = 1) -> str:
    """构造搜狗微信搜索 URL。

    type=2 表示搜索公众号文章（type=1 搜索公众号本身）
    """
    return f"{SOGOU_WEIXIN_URL}?type=2&query={quote(query)}&ie=utf8&page={page}"


def _get_headers() -> dict:
    """返回模拟浏览器的请求头。"""
    return {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://weixin.sogou.com/",
        "Connection": "keep-alive",
    }


def _parse_results(html: str) -> list:
    """解析搜狗微信搜索结果页 HTML，提取文章元数据。

    搜狗微信搜索结果结构：
    <div class="news-box">
      <div class="txt-box">
        <h3><a href="/link?url=...">文章标题</a></h3>
        <p class="txt-info">文章摘要...</p>
        <div class="s-p">
          <a class="account">公众号名称</a>
          <span class="s2">发布时间</span>
        </div>
      </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for box in soup.find_all("div", class_="txt-box"):
        item = {}

        # 标题和搜狗跳转链接
        h3 = box.find("h3")
        if h3:
            a = h3.find("a")
            if a:
                # 清理 HTML 注释标签（搜狗用 <!--red_beg--> 标记高亮）
                item["title"] = a.get_text(strip=True)
                sogou_link = a.get("href", "")
                if sogou_link.startswith("/"):
                    item["sogou_link"] = urljoin("https://weixin.sogou.com", sogou_link)
                else:
                    item["sogou_link"] = sogou_link

        # 摘要
        p = box.find("p", class_="txt-info")
        if p:
            item["abstract"] = p.get_text(strip=True)

        # 公众号名称：在 div.s-p > span.all-time-y2 中
        account_div = box.find("div", class_="s-p")
        if account_div:
            # 方式1: span.all-time-y2
            account_span = account_div.find("span", class_="all-time-y2")
            if account_span:
                item["account"] = account_span.get_text(strip=True)
            # 方式2: a.account（备选）
            if not item.get("account"):
                account_a = account_div.find("a")
                if account_a:
                    item["account"] = account_a.get_text(strip=True)

        # 发布时间：搜狗用 JS timeConvert(timestamp) 动态渲染，从 script 中提取时间戳
        time_span = box.find("span", class_="s2")
        if time_span:
            time_script = time_span.find("script")
            if time_script:
                # 提取 timeConvert('1234567890') 中的时间戳
                m = re.search(r"timeConvert\('(\d+)'\)", time_script.string or "")
                if m:
                    ts = int(m.group(1))
                    item["timestamp"] = ts
                    item["time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            else:
                # 如果没有 script，直接取文本
                t = time_span.get_text(strip=True)
                if t:
                    item["time"] = t

        # 文章来源标记（原创/转载等）
        src_tag = box.find("span", class_="wx-tag") or box.find("span", class_="src-tag")
        if src_tag:
            item["source_tag"] = src_tag.get_text(strip=True)

        if item.get("title"):
            # 补充元数据
            item["source"] = "wechat_sogou"
            item["oa"] = False  # 微信文章不是 OA 学术文献
            item["search_engine"] = "sogou_weixin"
            item["search_time"] = datetime.now().isoformat()
            results.append(item)

    return results


def search(query: str, limit: int = 10, session: requests.Session = None) -> list:
    """搜索微信公众号文章。

    Args:
        query: 搜索关键词
        limit: 返回结果数量（每页约 10 条，自动翻页）
        session: 可选的 requests.Session

    Returns:
        文章列表 [{"title", "abstract", "account", "time", "sogou_link", ...}, ...]
    """
    sess = session or requests.Session()
    sess.headers.update(_get_headers())

    all_results = []
    pages_needed = (limit + 9) // 10  # 每页约 10 条

    for page in range(1, pages_needed + 1):
        url = _build_search_url(query, page)
        print(f"[微信搜索] 第 {page} 页: {query}", file=sys.stderr)

        try:
            resp = sess.get(url, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"[微信搜索] 请求失败: {e}", file=sys.stderr)
            break

        # 检查是否被反爬拦截
        if resp.status_code != 200:
            print(f"[微信搜索] HTTP {resp.status_code}，可能被拦截", file=sys.stderr)
            break

        # 检查是否被重定向到反爬页面
        if "antispider" in resp.url or "verify" in resp.url:
            print(f"[微信搜索] 被反爬拦截（重定向到验证页面），停止翻页", file=sys.stderr)
            break

        # 解析结果
        page_results = _parse_results(resp.text)
        if not page_results:
            print(f"[微信搜索] 第 {page} 页无结果，停止", file=sys.stderr)
            break

        all_results.extend(page_results)
        print(f"[微信搜索] 第 {page} 页: {len(page_results)} 条结果", file=sys.stderr)

        # 如果不是最后一页，等待一段时间
        if page < pages_needed:
            time.sleep(SEARCH_GAP)

    # 去重（按标题）
    seen_titles = set()
    unique_results = []
    for r in all_results:
        title = r.get("title", "")
        if title not in seen_titles:
            seen_titles.add(title)
            unique_results.append(r)

    print(f"[微信搜索] 「{query}」共 {len(unique_results)} 条（去重后）", file=sys.stderr)
    return unique_results[:limit]


def search_multiple(queries: list, limit_per_query: int = 10,
                    session: requests.Session = None) -> list:
    """搜索多个关键词并合并去重。

    Args:
        queries: 关键词列表
        limit_per_query: 每个关键词的结果数量
        session: 可选的 requests.Session

    Returns:
        合并去重后的文章列表
    """
    sess = session or requests.Session()
    all_results = []
    seen_titles = set()

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(SEARCH_GAP)

        results = search(query, limit_per_query, sess)
        for r in results:
            title = r.get("title", "")
            if title not in seen_titles:
                seen_titles.add(title)
                r["search_query"] = query
                all_results.append(r)

    print(f"[微信搜索] 多关键词合并: {len(all_results)} 条（{len(queries)} 个关键词）", file=sys.stderr)
    return all_results


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="搜狗微信公众号文章搜索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本搜索
  python search_wechat.py "自甘风险 民法典1176"

  # 指定结果数量
  python search_wechat.py "业法财融合" --limit 20

  # 搜索并保存为 JSON
  python search_wechat.py "重大过失 体育侵权" -o results.json

  # 多关键词搜索
  python search_wechat.py "自甘风险 体育" "重大过失 竞技体育" --limit 10
        """)
    parser.add_argument("queries", nargs="+", help="搜索关键词（支持多个）")
    parser.add_argument("--limit", "-l", type=int, default=10,
                        help="每个关键词返回结果数量 (默认 10)")
    parser.add_argument("--output", "-o", help="保存为 JSON 文件 (默认输出到 stdout)")
    args = parser.parse_args()

    if len(args.queries) == 1:
        results = search(args.queries[0], args.limit)
    else:
        results = search_multiple(args.queries, args.limit)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"[微信搜索] 结果已保存: {args.output} ({len(results)} 条)", file=sys.stderr)
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
