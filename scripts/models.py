#!/usr/bin/env python3
"""统一文献数据模型。

用 TypedDict 定义所有检索脚本输出的统一格式，
确保检索→排序→下载→引文各环节的数据一致性。

这是系统的"序参量"——统一格式支配各子系统的协同。
"""
from typing import TypedDict, Optional, Union


class Paper(TypedDict, total=False):
    """统一文献格式——所有检索脚本的输出必须遵守此 schema。"""
    # ── 必填字段 ──
    title: str           # 文献标题
    authors: str         # 作者列表（逗号分隔，最多3人+et al.）
    year: Optional[str]  # 发表年份

    # ── 来源字段 ──
    source: str          # 期刊/会议/来源名
    doi: str             # DOI（如有）
    url: str             # 文献页面 URL

    # ── 开放获取字段 ──
    # oa 字段三值语义（修复 v0.1.0 硬编码 True 的问题）：
    #   True            → 真 OA，无需认证可直接下载
    #   "ncpssd_login"  → 条件 OA，需 NCPSSD 登录后下载
    #   False           → 非OA，需机构权限
    oa: Union[bool, str]
    pdf: str             # PDF 直链（如有）

    # ── 评分字段 ──
    cited_by_count: int  # 被引次数
    relevance_score: float  # 相关性分数（由排序模块计算）

    # ── NCPSSD 专有字段 ──
    lngid: str           # NCPSSD 文章唯一标识
    abstract: str        # 摘要
    issue: str           # 期号
    file_name: str       # NCPSSD PDF 文件名
    type: str            # 文献类型

    # ── 下载状态字段（由下载模块写入）──
    download_status: str   # downloaded / error / locked / not-pdf
    download_path: str     # 本地文件路径
    download_note: str     # 下载备注/错误信息
    download_source: str   # 实际下载来源（ncpssd / unpaywall / crossref）


def validate_paper(p: dict) -> bool:
    """检查 dict 是否符合 Paper schema 的最低要求。

    Returns: True 如果包含至少 title 字段
    """
    return bool(p.get("title"))


def normalize_paper(p: dict) -> dict:
    """将任意 dict 归一化为符合 Paper schema 的 dict。

    补全缺失字段，统一字段类型。
    """
    return {
        "title": p.get("title", ""),
        "authors": p.get("authors", ""),
        "year": str(p.get("year", ""))[:4] if p.get("year") else "",
        "source": p.get("source", ""),
        "doi": p.get("doi", ""),
        "url": p.get("url", ""),
        "oa": p.get("oa", False),
        "pdf": p.get("pdf", ""),
        "cited_by_count": p.get("cited_by_count", 0),
        "lngid": p.get("lngid", ""),
        "abstract": p.get("abstract", ""),
        "issue": p.get("issue", ""),
        "file_name": p.get("file_name", ""),
        "type": p.get("type", ""),
    }
