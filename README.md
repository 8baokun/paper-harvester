# Paper Harvester（论文收割机）

免费学术文献检索 + OA 全文自动下载的命令行工具集。

支持多源检索（OpenAlex / Semantic Scholar / Crossref / arXiv / NCPSSD）、8级降级链自动下载 OA PDF、国家哲社文献中心登录下载中文论文、微信公众号文章正文提取。

## 功能特性

| 能力 | 说明 |
|---|---|
| **英文 OA 检索** | OpenAlex / Semantic Scholar / Crossref / arXiv 四源并行检索 |
| **NCPSSD 检索+下载** | 国家哲学社会科学文献中心，登录后自动下载中文论文 PDF |
| **8级降级链** | 原始URL → Unpaywall → Crossref → OpenAlex → Semantic Scholar → SSRN → Europe PMC → arXiv |
| **微信公众号文章** | 提取正文转 Markdown，支持图片下载和批量处理 |
| **PDF 深度验证** | `%PDF-` 签名校验 + PyMuPDF 文本提取 + 标题关键词匹配（防 HTML 伪装） |
| **原子写入** | 所有文件 `.part → rename`，防中断产生半截文件 |
| **优雅中断** | Ctrl+C 保存已完成结果后退出 |
| **JSON Sidecar** | 每篇 PDF 自动附带 `.json` 元数据文件 |
| **并发下载** | `--parallel --workers N` 并发下载（最大 4 并发） |
| **五维排序** | 相关性 0.35 / 被引 0.25 / 时效 0.20 / 来源等级 0.10 / OA 可得性 0.10 |
| **BibTeX 生成** | 自动生成含本地 PDF 路径注释的 BibTeX 文件 |

## 目录结构

```
paper-harvester/
├── SKILL.md                        # Agent 技能说明（Agent 读取入口）
├── README.md                       # 本文件
├── .env.example                    # 环境变量模板（复制为 .env 填入真实凭证）
├── .gitignore
├── scripts/
│   ├── config.py                   # 全局配置（环境变量 + 常量集中管理）
│   ├── models.py                   # 统一文献数据模型（Paper TypedDict）
│   ├── retry.py                    # 指数退避重试装饰器
│   ├── sort_papers.py              # 五维权重排序
│   ├── build_bib.py                # BibTeX 生成
│   ├── search_openalex.py          # OpenAlex 检索
│   ├── search_semantic_scholar.py  # Semantic Scholar 检索
│   ├── search_crossref.py          # Crossref 检索
│   ├── search_arxiv.py             # arXiv 检索（天然 OA）
│   ├── search_ncpssd.py            # ★ NCPSSD 检索 + 登录 + PDF 下载
│   ├── download_oa.py              # ★ OA PDF 自动下载器（8级降级链）
│   └── download_wechat.py          # ★ 微信公众号文章下载器
├── references/
│   ├── oa-routing.md               # OA 路由与合规边界
│   └── chinese-sources.md          # 中文免费源说明
└── reviews/
    ├── system-science-review-v0.1.0.md    # 系统科学审核报告
    └── comparative-analysis-v0.2.0.md      # 对标调研报告
```

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/8baokun/paper-harvester.git
cd paper-harvester
```

### 2. 安装依赖

```bash
pip install requests beautifulsoup4 html2text PyMuPDF
```

### 3. 配置凭证（NCPSSD 下载需要）

```bash
cp .env.example .env
# 编辑 .env，填入你的 NCPSSD 账号密码
```

`.env` 文件内容：

```ini
# NCPSSD 国家哲学社会科学文献中心
NCPSSD_USERNAME=你的手机号或邮箱
NCPSSD_PASSWORD=你的密码

# Semantic Scholar API key（可选，提高限额）
# 申请地址：https://www.semanticscholar.org/product/api
PH_S2_API_KEY=
```

> **安全提醒**：`.env` 已在 `.gitignore` 中排除，不会被提交到 git。请勿将真实凭证硬编码到代码中。

NCPSSD 注册地址：https://www.ncpssd.cn/register （免费注册，无验证码）

## 使用方法

### NCPSSD 检索 + 下载

```bash
# 仅检索（无需登录）
python scripts/search_ncpssd.py "业法财融合" 20

# 按期刊名检索
python scripts/search_ncpssd.py --journal "法学研究" 20

# 检索 + 下载（账号从 .env 读取）
python scripts/search_ncpssd.py "业法财融合" 20 --download

# 检索 + 并发下载
python scripts/search_ncpssd.py "法学理论" 10 --download --parallel --workers 3

# 使用 Cookie 持久化（首次登录后免登录）
python scripts/search_ncpssd.py "法学理论" 10 --download --cookie ncpssd_cookies.json

# 从已有 JSON 结果批量下载
cat results.json | python scripts/search_ncpssd.py --stdin --download
```

**NCPSSD 下载策略**：
- 登录：`POST /login`，密码 MD5 加密（与前端 `$.md5()` 一致）
- 下载链路：`readurl` 获取 PDF URL → `getMinioSign` 获取签名 → 带签名头下载
- 下载间隔默认 30 秒/篇（`PH_NCPSSD_DL_GAP` 可调），防封号
- 文件命名：`{年}_{第一作者}_{标题前30字}.pdf`

### 英文 OA 文献检索 + 下载

```bash
# OpenAlex 检索
python scripts/search_openalex.py "digital government" 20

# Semantic Scholar 检索
python scripts/search_semantic_scholar.py "compliance management" 20

# Crossref 检索
python scripts/search_crossref.py "state-owned enterprise" 20

# arXiv 检索（天然 OA）
python scripts/search_arxiv.py "legal reasoning" 20

# 从 JSON 结果下载 OA PDF（8级降级链）
echo '[{"title":"...","authors":"...","year":2022,"doi":"...","oa":true,"pdf":"https://..."}]' \
  | python scripts/download_oa.py

# 并发下载
echo '[...]' | python scripts/download_oa.py --parallel --workers 3
```

**8级降级链**：

```
原始 PDF URL → Unpaywall → Crossref → OpenAlex
→ Semantic Scholar → SSRN → Europe PMC → arXiv → 🔒 locked
```

每级失败自动降级到下一源，PDF 落盘前校验 `%PDF-` 签名 + PyMuPDF 文本提取验证。

### 微信公众号文章下载

```bash
# 单篇文章 → Markdown
python scripts/download_wechat.py "https://mp.weixin.qq.com/s/xxxxx"

# 从分享文本提取 URL 并下载
python scripts/download_wechat.py --share-text "文章标题 https://mp.weixin.qq.com/s/xxxxx"

# 带图片下载 + HTML
python scripts/download_wechat.py "https://mp.weixin.qq.com/s/xxxxx" -o ./articles --html --download-images

# 批量下载（从文件读取 URL 列表）
python scripts/download_wechat.py --input urls.txt -o ./articles --download-images --workers 3
```

### 文献排序 + BibTeX 生成

```bash
# 五维权重排序
cat results.json | python scripts/sort_papers.py "检索关键词" --limit 10

# 生成 BibTeX（含本地 PDF 路径注释）
cat papers.json | python scripts/build_bib.py > lit.bib
```

## 配置

所有参数集中在 `scripts/config.py`，支持环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PH_UA` | Chrome 120 UA | HTTP User-Agent |
| `PH_EMAIL` | paper-harvester@example.com | API 联系邮箱 |
| `PH_TIMEOUT` | 20 | 请求超时（秒） |
| `PH_DL_TIMEOUT` | 30 | 下载超时（秒） |
| `PH_MIN_GAP` | 2.0 | OA 源下载最小间隔（秒） |
| `PH_MAX_PER_MIN` | 20 | 每分钟最大下载数 |
| `PH_NCPSSD_DL_GAP` | 30.0 | NCPSSD 下载间隔（秒，防封号） |
| `PH_MAX_RETRIES` | 3 | 最大重试次数 |
| `PH_BACKOFF` | 0.5 | 指数退避因子 |
| `PH_OUT_DIR` | pdfs | PDF 输出目录 |
| `PH_WECHAT_OUT` | wechat_articles | 微信文章输出目录 |
| `PH_COOKIE_FILE` | ncpssd_cookies.json | NCPSSD Cookie 文件路径 |
| `PH_S2_API_KEY` | （空） | Semantic Scholar API key（可选） |
| `NCPSSD_USERNAME` | （空） | NCPSSD 账号 |
| `NCPSSD_PASSWORD` | （空） | NCPSSD 密码 |

## 统一数据模型

所有检索脚本输出遵循 `scripts/models.py` 定义的 `Paper` TypedDict：

```python
class Paper(TypedDict, total=False):
    title: str                    # 文献标题
    authors: str                  # 作者列表
    year: Optional[str]           # 发表年份
    source: str                   # 期刊/会议名
    doi: str                      # DOI
    oa: Union[bool, str]          # True=OA / "ncpssd_login"=条件OA / False=非OA
    pdf: str                      # PDF 直链
    cited_by_count: int           # 被引次数
    lngid: str                    # NCPSSD 文章标识
    # ... 详见 models.py
```

`oa` 字段三值语义确保检索 → 排序 → 下载链路无语义漂移。

## 合规声明

- **NCPSSD**：注册后免费下载，不绕过验证码、不批量镜像
- **微信公众号**：只提取公开可访问的文章，尊重版权
- **OA 论文**：只下载合法开放获取的 PDF，不尝试破解付费墙
- **付费墙论文**：只提供 DOI 落地页 + 机构访问路径提示（WebVPN / CARSI / 文献传递）
- **禁止**：默认走 Sci-Hub / LibGen 等盗版镜像、伪造 DOI、连续批量下载或镜像平台内容
- 下载内容仅限个人学习研究，遵守各平台服务条款

详见 `references/oa-routing.md`。

## 版本历史

| 版本 | 日期 | 主要变更 |
|---|---|---|
| v0.4.0 | 2026-10-15 | 8级多源降级链、Semantic Scholar OA 源、SSRN 法学预印本源、NCPSSD 下载流程修正 |
| v0.3.0 | 2026-10-15 | 并发下载、JSON Sidecar、原子写入、优雅中断、PDF 深度验证、期刊检索 |
| v0.2.0 | 2026-10-15 | NCPSSD 登录下载、微信公众号文章提取、配置化、统一数据模型 |
| v0.1.0 | 2026-10-15 | 初始版本：OpenAlex/Crossref/arXiv 检索 + OA PDF 下载 |

## License

MIT
