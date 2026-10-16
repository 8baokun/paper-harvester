#!/usr/bin/env python3
"""paper-harvester 全局配置。

将各脚本中分散的硬编码参数集中到此处，支持环境变量覆盖。
这是系统从"纯他组织"向"半自组织"过渡的基础——
参数可配置意味着系统具备了根据运行经验自我调整的能力。
"""
import os

# ── .env 加载 ─────────────────────────────────────────
# 从脚本同目录的 ../.env 读取凭证（.env 不进 git）
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# ── 网络请求 ──────────────────────────────────────────
UA = os.environ.get("PH_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
EMAIL = os.environ.get("PH_EMAIL", "paper-harvester@example.com")
UA_API = f"paper-harvester/0.4 (mailto:{EMAIL})"
REQUEST_TIMEOUT = int(os.environ.get("PH_TIMEOUT", "20"))
DOWNLOAD_TIMEOUT = int(os.environ.get("PH_DL_TIMEOUT", "30"))

# ── 下载限速 ──────────────────────────────────────────
MIN_GAP = float(os.environ.get("PH_MIN_GAP", "2.0"))  # OA 源每篇最小间隔秒
MAX_PER_MIN = int(os.environ.get("PH_MAX_PER_MIN", "20"))  # 每分钟最大下载数
IMG_DELAY = float(os.environ.get("PH_IMG_DELAY", "0.3"))  # 图片下载间隔

# NCPSSD 限速（防 WAF 拦截）
NCPSSD_SEARCH_GAP_MIN = float(os.environ.get("PH_NCPSSD_SEARCH_GAP_MIN", "20.0"))  # 搜索最小间隔秒
NCPSSD_SEARCH_GAP_MAX = float(os.environ.get("PH_NCPSSD_SEARCH_GAP_MAX", "60.0"))  # 搜索最大间隔秒
NCPSSD_DOWNLOAD_GAP = float(os.environ.get("PH_NCPSSD_DL_GAP", "30.0"))  # 下载最小间隔秒

# ── 重试与降级 ────────────────────────────────────────
MAX_RETRIES = int(os.environ.get("PH_MAX_RETRIES", "3"))
BACKOFF_FACTOR = float(os.environ.get("PH_BACKOFF", "0.5"))  # 指数退避因子
RETRY_STATUS = {429, 500, 502, 503, 504}  # 触发重试的 HTTP 状态码

# ── 检索默认值 ────────────────────────────────────────
MAX_RESULTS_DEFAULT = int(os.environ.get("PH_MAX_RESULTS", "20"))
PAGE_SIZE_MAX = 50  # NCPSSD API 单页最大返回数

# ── 输出目录 ──────────────────────────────────────────
OUT_DIR = os.environ.get("PH_OUT_DIR", "pdfs")
WECHAT_OUT = os.environ.get("PH_WECHAT_OUT", "wechat_articles")
COOKIE_FILE = os.environ.get("PH_COOKIE_FILE", "ncpssd_cookies.json")
LOG_FILE = os.environ.get("PH_LOG_FILE", "")  # 空则不写日志文件

# ── NCPSSD ────────────────────────────────────────────
NCPSSD_BASE = "https://www.ncpssd.cn"
NCPSSD_LOGIN_URL = f"{NCPSSD_BASE}/login"  # POST 登录端点（loginType=account, password=MD5）
NCPSSD_SEARCH_API = f"{NCPSSD_BASE}/searchHandler/search"
NCPSSD_READ_URL = f"{NCPSSD_BASE}/Literature/readurl"  # GET ?id={lngid} → JSON {"url": "..."}
NCPSSD_MINIO_SIGN_URL = f"{NCPSSD_BASE}/common/getMinioSign"  # POST → JSON {"code": 200, "data": "sign"}
NCPSSD_JOURNAL_API = f"{NCPSSD_BASE}/articleinfoHandler/getjournalarticletable"
NCPSSD_USERNAME = os.environ.get("NCPSSD_USERNAME", "")
NCPSSD_PASSWORD = os.environ.get("NCPSSD_PASSWORD", "")

# ── API 端点 ──────────────────────────────────────────
OPENALEX_API = "https://api.openalex.org/works"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_API_KEY = os.environ.get("PH_S2_API_KEY", "")  # Semantic Scholar API key（可选，提高限额）
CROSSREF_API = "https://api.crossref.org/works"
ARXIV_API = "http://export.arxiv.org/api/query"
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"

# ── SSRN（法学预印本主阵地）────────────────────────────
SSRN_SEARCH_URL = "https://papers.ssrn.com/sol3/results.cfm"
SSRN_PDF_BASE = "https://papers.ssrn.com/sol3/Delivery.cfm"

# ── 排序权重（SKILL.md 定义的五维权重）────────────────
SORT_WEIGHTS = {
    "relevance": 0.35,   # 相关性（关键词匹配度）
    "citation": 0.25,    # 被引次数
    "recency": 0.20,     # 时效性（发表年份）
    "source": 0.10,      # 来源质量（期刊/会议级别）
    "oa": 0.10,          # 开放获取程度
}

# ── 来源质量评分表 ────────────────────────────────────
SOURCE_TIER = {
    # S 级（顶级期刊/会议）
    "S": 1.0,
    # A 级（核心期刊）
    "A": 0.8,
    # B 级（普通期刊）
    "B": 0.6,
    # C 级（会议/预印本）
    "C": 0.4,
    # 未分级
    "unknown": 0.3,
}


def get_source_tier(journal: str) -> float:
    """根据期刊名返回来源质量分数。

    简单实现：CSSCI/北大核心期刊列表未内置，统一返回 unknown。
    后续可接入期刊分级数据。
    """
    if not journal:
        return SOURCE_TIER["unknown"]
    # 预留：后续可接入 CSSCI 期刊列表
    return SOURCE_TIER["unknown"]
