#!/usr/bin/env python3
"""微信公众号文章下载器。

从 mp.weixin.qq.com 文章链接提取正文内容，保存为 Markdown / HTML。

用法:
  # 下载单篇文章为 Markdown
  python download_wechat.py "https://mp.weixin.qq.com/s/xxxxx"

  # 指定输出目录
  python download_wechat.py "https://mp.weixin.qq.com/s/xxxxx" -o ./articles

  # 同时保存 HTML
  python download_wechat.py "https://mp.weixin.qq.com/s/xxxxx" --html

  # 批量下载（从文件读取 URL）
  python download_wechat.py --input urls.txt -o ./articles

  # 下载并嵌入图片
  python download_wechat.py "https://mp.weixin.qq.com/s/xxxxx" --download-images

依赖: requests, beautifulsoup4, html2text
  pip install requests beautifulsoup4 html2text
"""
import sys, os, json, re, time, argparse, hashlib, signal
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString
except ImportError:
    print("[ERROR] 需要 requests 和 beautifulsoup4: pip install requests beautifulsoup4", file=sys.stderr)
    sys.exit(1)

try:
    import html2text
    HAS_H2T = True
except ImportError:
    HAS_H2T = False
    print("[WARN] html2text 未安装，将输出 HTML 格式。安装: pip install html2text", file=sys.stderr)

from config import UA, WECHAT_OUT as DEFAULT_OUT, IMG_DELAY, REQUEST_TIMEOUT, MAX_RETRIES
from retry import retry_with_backoff


def _safe_filename(s: str, max_len: int = 80) -> str:
    """生成安全的文件名。"""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", s).strip()
    return s[:max_len] if len(s) > max_len else s


def _atomic_write_text(filepath: str, text: str, encoding: str = "utf-8"):
    """原子写入文本文件：先写 .part 文件，成功后 rename。"""
    part_path = filepath + ".part"
    try:
        with open(part_path, "w", encoding=encoding) as f:
            f.write(text)
        os.rename(part_path, filepath)
    except Exception:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise


def _atomic_write_bytes(filepath: str, data: bytes):
    """原子写入二进制文件：先写 .part 文件，成功后 rename。"""
    part_path = filepath + ".part"
    try:
        with open(part_path, "wb") as f:
            f.write(data)
        os.rename(part_path, filepath)
    except Exception:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise


def _write_wechat_sidecar(filepath: str, article: dict, url: str, img_count: int = 0):
    """写入 JSON sidecar 元数据文件。"""
    sidecar_path = filepath.rsplit(".", 1)[0] + "_meta.json"
    meta = {
        "title": article.get("title", ""),
        "author": article.get("author", ""),
        "publish_time": article.get("publish_time", ""),
        "abstract": article.get("abstract", ""),
        "url": url,
        "download_source": "wechat",
        "download_time": datetime.now().isoformat(),
        "local_path": os.path.abspath(filepath),
        "images_count": img_count,
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 优雅中断 ───────────────────────────────────────────
_wechat_interrupted = False

def _signal_handler(signum, frame):
    global _wechat_interrupted
    _wechat_interrupted = True
    print("\n[微信] 收到中断信号，正在优雅退出...", file=sys.stderr)


def _clean_url(url: str) -> str:
    """清理微信文章 URL，移除追踪参数。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    # 保留必要参数
    keep = {}
    for key in ["__biz", "mid", "idx", "sn", "chksm", "scene", "subscene"]:
        if key in qs:
            keep[key] = qs[key][0]
    # 短链接直接返回
    if "/s/" in url and not keep:
        return url.split("?")[0]
    clean_query = "&".join(f"{k}={v}" for k, v in keep.items())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{clean_query}" if clean_query else url


def extract_url_from_share_text(text: str) -> str:
    """从微信分享文本中提取文章 URL。

    微信分享文本格式示例：
    "我在微信读过这篇文章：标题 https://mp.weixin.qq.com/s/xxxxx"
    "标题 作者 2024-10-15 https://mp.weixin.qq.com/s?__biz=xxx&mid=xxx"

    Returns: 提取到的 URL，或空字符串
    """
    if not text:
        return ""
    # 匹配 mp.weixin.qq.com 的 URL（短链和长链）
    patterns = [
        r'https?://mp\.weixin\.qq\.com/s[/?][^\s<>"\']+',
        r'https?://mp\.weixin\.qq\.com/s\?[^\s<>"\']+',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).rstrip(",.;!?")
    return ""


def fetch_article(url: str, session: requests.Session = None) -> dict:
    """获取微信公众号文章内容。

    Returns:
        {
            "title": str,
            "author": str,        # 公众号名称
            "account": str,       # 公众号 ID
            "publish_time": str,  # 发布时间
            "url": str,
            "html": str,          # 清洗后的正文 HTML
            "soup": BeautifulSoup, # 原始 soup
        }
    """
    sess = session or requests.Session()
    sess.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    url = _clean_url(url)
    resp = sess.get(url, timeout=20)
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── 提取标题 ──
    title = ""
    title_tag = soup.find("h1", id="activity-name") or soup.find("h1", class_="rich_media_title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "")
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "未命名文章"

    # ── 提取公众号名称 ──
    author = ""
    # 方式1: profile_nickname
    nick_tag = soup.find("a", id="js_name") or soup.find("span", class_="profile_nickname")
    if nick_tag:
        author = nick_tag.get_text(strip=True)
    # 方式2: meta
    if not author:
        meta_author = soup.find("meta", property="og:article:author") or soup.find("meta", attrs={"name": "author"})
        if meta_author:
            author = meta_author.get("content", "")
    # 方式3: 脚本变量
    if not author:
        for script in soup.find_all("script"):
            text = script.string or ""
            m = re.search(r'nickname\s*[:=]\s*["\']([^"\']+)["\']', text)
            if m:
                author = m.group(1)
                break

    # ── 提取发布时间 ──
    publish_time = ""
    time_tag = soup.find("em", id="publish_time") or soup.find("em", class_="publish_time")
    if time_tag:
        publish_time = time_tag.get_text(strip=True)
    if not publish_time:
        # 从 meta 或脚本中提取
        meta_time = soup.find("meta", property="og:article:published_time") or soup.find("meta", property="article:published_time")
        if meta_time:
            publish_time = meta_time.get("content", "")
    if not publish_time:
        for script in soup.find_all("script"):
            text = script.string or ""
            m = re.search(r'publish_time\s*[:=]\s*["\']([^"\']+)["\']', text)
            if m:
                publish_time = m.group(1)
                break
        if not publish_time:
            m = re.search(r'var\s+createTime\s*=\s*[\'"]([^\'"]+)[\'"]', resp.text)
            if m:
                publish_time = m.group(1)

    # ── 提取正文 HTML ──
    content_div = soup.find("div", id="js_content") or soup.find("div", class_="rich_media_content")
    content_html = ""
    if content_div:
        # 清理无用元素
        for tag in content_div.find_all(["script", "style", "iframe"]):
            tag.decompose()
        # 移除空的段落
        for p in content_div.find_all("p"):
            if not p.get_text(strip=True) and not p.find("img"):
                p.decompose()
        content_html = str(content_div)

    # ── 提取文章摘要 ──
    abstract = ""
    meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        abstract = meta_desc.get("content", "")

    return {
        "title": title,
        "author": author,
        "account": "",
        "publish_time": publish_time,
        "abstract": abstract,
        "url": url,
        "html": content_html,
        "soup": soup,
    }


def html_to_markdown(html: str, title: str = "", author: str = "", publish_time: str = "",
                     url: str = "", abstract: str = "") -> str:
    """将 HTML 转换为 Markdown。"""
    if not HAS_H2T:
        return f"<!-- html2text 未安装，以下为原始 HTML -->\n{html}"

    h = html2text.HTML2Text()
    h.body_width = 0  # 不自动换行
    h.unicode_snob = True
    h.protect_links = True
    h.wrap_links = False
    h.mark_code = True  # 保留代码块

    md_body = h.handle(html)

    # 构建 Markdown 文档
    parts = []
    parts.append(f"# {title}\n")

    # 元信息
    meta_lines = []
    if author:
        meta_lines.append(f"> **公众号**: {author}")
    if publish_time:
        meta_lines.append(f"> **发布时间**: {publish_time}")
    if url:
        meta_lines.append(f"> **原文链接**: {url}")
    if abstract:
        meta_lines.append(f"> **摘要**: {abstract}")
    if meta_lines:
        parts.append("\n".join(meta_lines) + "\n")

    parts.append("---\n")
    parts.append(md_body.strip())

    return "\n".join(parts)


def download_images(soup_or_html, session: requests.Session, img_dir: str,
                    article_url: str = "") -> dict:
    """下载文章中的图片到本地。

    Returns: {original_url: local_path} 映射
    """
    if isinstance(soup_or_html, str):
        soup = BeautifulSoup(soup_or_html, "html.parser")
    else:
        soup = soup_or_html

    os.makedirs(img_dir, exist_ok=True)
    url_map = {}
    img_count = 0

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or src.startswith("data:"):
            continue

        # 处理相对 URL
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(article_url, src)

        # 下载图片
        try:
            time.sleep(IMG_DELAY)
            headers = {"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/"}
            resp = session.get(src, timeout=15, headers=headers)
            if resp.status_code != 200:
                continue

            # 确定扩展名
            content_type = resp.headers.get("Content-Type", "")
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"
            elif "webp" in content_type:
                ext = ".webp"
            elif "svg" in content_type:
                ext = ".svg"

            img_count += 1
            img_name = f"image_{img_count:03d}{ext}"
            img_path = os.path.join(img_dir, img_name)
            _atomic_write_bytes(img_path, resp.content)

            # 替换 src 为本地路径
            img["src"] = f"images/{img_name}"
            # 移除 data-src 避免重复
            if img.get("data-src"):
                del img["data-src"]

            url_map[src] = f"images/{img_name}"
            print(f"  [图片] {img_name} ({len(resp.content)} bytes)", file=sys.stderr)

        except Exception as e:
            print(f"  [图片] 下载失败: {src[:50]}... ({e})", file=sys.stderr)

    return url_map


def save_article(url: str, out_dir: str = DEFAULT_OUT, save_html: bool = False,
                 save_json: bool = False, download_imgs: bool = False,
                 session: requests.Session = None) -> dict:
    """下载并保存一篇微信公众号文章。

    Returns:
        {"title": ..., "md_path": ..., "html_path": ..., "json_path": ..., "images": int}
    """
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": UA})

    print(f"[微信] 获取文章: {url[:80]}...", file=sys.stderr)
    article = fetch_article(url, sess)

    if not article["html"]:
        print(f"[微信] 未提取到正文内容，可能页面需要 JS 渲染或链接已失效", file=sys.stderr)
        return {"error": "无法提取正文", "url": url}

    title = article["title"]
    safe_title = _safe_filename(title)

    # 创建文章目录
    article_dir = os.path.join(out_dir, safe_title)
    os.makedirs(article_dir, exist_ok=True)

    # 下载图片
    img_count = 0
    content_html = article["html"]
    if download_imgs:
        print(f"[微信] 下载图片...", file=sys.stderr)
        content_soup = BeautifulSoup(content_html, "html.parser")
        img_dir = os.path.join(article_dir, "images")
        download_images(content_soup, sess, img_dir, url)
        content_html = str(content_soup)
        img_count = len(content_soup.find_all("img"))

    # 保存 Markdown
    md_content = html_to_markdown(
        content_html,
        title=title,
        author=article["author"],
        publish_time=article["publish_time"],
        url=url,
        abstract=article["abstract"],
    )
    md_path = os.path.join(article_dir, f"{safe_title}.md")
    _atomic_write_text(md_path, md_content)
    print(f"[微信] Markdown 已保存: {md_path}", file=sys.stderr)

    # 自动写入 JSON sidecar 元数据（不再需要 --json 标志）
    _write_wechat_sidecar(md_path, article, url, img_count)

    result = {
        "title": title,
        "author": article["author"],
        "publish_time": article["publish_time"],
        "url": url,
        "md_path": os.path.abspath(md_path),
        "images": img_count,
    }

    # 保存 HTML
    if save_html:
        html_path = os.path.join(article_dir, f"{safe_title}.html")
        # 构建完整 HTML 文档
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ max-width: 677px; margin: 0 auto; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; line-height: 1.75; color: #333; }}
img {{ max-width: 100%; height: auto; }}
h1 {{ font-size: 24px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p><strong>{article['author']}</strong> | {article['publish_time']}</p>
<p>原文链接: <a href="{url}">{url}</a></p>
<hr>
{content_html}
</body>
</html>"""
        _atomic_write_text(html_path, full_html)
        result["html_path"] = os.path.abspath(html_path)
        print(f"[微信] HTML 已保存: {html_path}", file=sys.stderr)

    # 保存 JSON 元数据
    if save_json:
        json_path = os.path.join(article_dir, f"{safe_title}_info.json")
        meta = {
            "title": title,
            "author": article["author"],
            "publish_time": article["publish_time"],
            "abstract": article["abstract"],
            "url": url,
            "md_file": f"{safe_title}.md",
            "fetch_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "images_count": img_count,
        }
        _atomic_write_text(json_path, json.dumps(meta, ensure_ascii=False, indent=2))
        result["json_path"] = os.path.abspath(json_path)

    return result


def batch_download(urls_file: str, out_dir: str = DEFAULT_OUT, save_html: bool = False,
                   save_json: bool = False, download_imgs: bool = False,
                   workers: int = 1) -> list:
    """批量下载文章（支持优雅中断）。"""
    # 注册中断信号
    global _wechat_interrupted
    _wechat_interrupted = False
    signal.signal(signal.SIGINT, _signal_handler)

    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"[微信] 批量下载 {len(urls)} 篇文章", file=sys.stderr)
    results = []

    if workers <= 1:
        # 串行下载
        sess = requests.Session()
        sess.headers.update({"User-Agent": UA})
        for i, url in enumerate(urls):
            if _wechat_interrupted:
                print(f"[微信] 中断：已完成 {i}/{len(urls)}", file=sys.stderr)
                break
            print(f"\n[微信] ({i+1}/{len(urls)})", file=sys.stderr)
            try:
                result = save_article(url, out_dir, save_html, save_json, download_imgs, sess)
                results.append(result)
            except Exception as e:
                results.append({"error": str(e), "url": url})
                print(f"[微信] 下载失败: {url[:50]}... ({e})", file=sys.stderr)
            time.sleep(2)  # 批量间隔
    else:
        # 并行下载
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(save_article, url, out_dir, save_html, save_json, download_imgs): url
                       for url in urls}
            for future in as_completed(futures):
                if _wechat_interrupted:
                    print("[微信] 中断：跳过剩余任务", file=sys.stderr)
                    break
                url = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"error": str(e), "url": url})

    if _wechat_interrupted:
        print(f"[微信] 下载被中断。已完成 {len(results)}/{len(urls)} 篇。", file=sys.stderr)

    return results


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="微信公众号文章下载器")
    parser.add_argument("url", nargs="?", help="文章 URL")
    parser.add_argument("-o", "--output", default=DEFAULT_OUT, help="输出目录 (默认: wechat_articles)")
    parser.add_argument("--html", action="store_true", help="同时保存 HTML 格式")
    parser.add_argument("--json", action="store_true", help="同时保存 JSON 元数据")
    parser.add_argument("--download-images", action="store_true", help="下载文章中的图片到本地")
    parser.add_argument("--input", help="批量下载: 包含 URL 列表的文件 (每行一个 URL)")
    parser.add_argument("--workers", type=int, default=1, help="批量下载并发数 (默认: 1)")
    parser.add_argument("--share-text", help="微信分享文本（自动提取其中的 URL）")
    args = parser.parse_args()

    # 处理分享文本
    if args.share_text:
        url = extract_url_from_share_text(args.share_text)
        if not url:
            print("[ERROR] 无法从分享文本中提取 URL", file=sys.stderr)
            sys.exit(1)
        print(f"[微信] 从分享文本提取 URL: {url}", file=sys.stderr)
        args.url = url

    if args.input:
        results = batch_download(args.input, args.output, args.html, args.json,
                                 args.download_images, args.workers)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.url:
        result = save_article(args.url, args.output, args.html, args.json, args.download_images)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.error("需要文章 URL、--input 文件或 --share-text 分享文本")


if __name__ == "__main__":
    main()
