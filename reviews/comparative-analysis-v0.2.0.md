# paper-harvester v0.2.0 对标调研报告（第二轮）

> 调研日期：2026-10-15
> 调研范围：GitHub / HuggingFace / Zotero 生态，共 15 个项目
> 对标对象：paper-harvester v0.2.0（原 paper-harvester）

---

## 一、项目全景对比矩阵

### 1.1 英文论文多源下载工具

| 维度 | **paper-harvester v0.2.0** | **DoiHunter v1.2.0** | **fulltext-article-downloader v0.2.4** | **Review Buddy** | **DoiHarvest** |
|------|---|---|---|---|---|
| **定位** | WorkBuddy Skill（中文法学场景） | PyPI 包 + CLI | PyPI 包 + CLI + MCP Server + Claude Code Plugin | 系统综述流水线 | Sci-Hub 批量下载 |
| **DOI 发现源** | OpenAlex / S2 / Crossref / arXiv | Crossref + OpenAlex + arXiv 三源合并排序 | Crossref（出版商识别） | Scopus / PubMed / arXiv / Google Scholar / IEEE | Excel 输入 DOI |
| **PDF 下载降级链** | 原始URL → Unpaywall → locked（3级） | Publisher → Unpaywall → OpenAlex → Europe PMC → arXiv → Legacy（6级） | Per-publisher 定制（14+ 出版商，最深6级） | 10+ 方法 + Zotero 翻译器 + Camoufox 浏览器 | Sci-Hub 域名池轮换 + Crossref + Unpaywall |
| **PDF 验证** | `%PDF-` 前5字节 | `%PDF-` 签名 + `.part` 原子写入 | `%PDF-` + **前两页文本提取 + Crossref 标题匹配** + SI 检测 | Zotero 翻译器验证 | `%PDF-` 签名 |
| **标题匹配** | ❌ 无 | ✅ 可配置阈值（默认0.92），三源合并排序 | ✅ Crossref 标题 vs PDF 前两页文本 | ❌ 无 | ❌ 无 |
| **Preprint 标记** | ❌ 无 | ❌ 无 | ✅ `note` 字段标注 preprint/accepted manuscript | ❌ 无 | ❌ 无 |
| **重试机制** | 指数退避（3次，0.5因子） | bounded retries + 超时 | 瞬时 429/5xx 重试 + UA 三层降级 | 重试 + 限速 | 多线程 + 随机延迟 |
| **并发下载** | ❌ 单线程 | ❌ 顺序批处理 | ✅ `workers` 参数 + 出版商限速 | ✅ 并行 | ✅ 多线程 |
| **反爬应对** | ❌ 无 | ❌ 无 | ✅ 浏览器UA → plain UA → Chrome TLS 指纹（curl-cffi） | ✅ **Camoufox 浏览器**（Elsevier 14%→90%） | 域名池轮换 |
| **失败记录** | ❌ 无 | ✅ `failed_downloads.txt` + source-specific 原因 | ✅ JSON 结果 + `attempts` 数组 + `--log-file` | ✅ 日志 | Excel 状态回写 |
| **中断处理** | ❌ 无 | ✅ 优雅 Ctrl+C + 清理 partial | ✅ 原子写入 | ✅ 缓存恢复 | ❌ 无 |
| **AI 筛选** | ❌ 无 | ❌ 无 | ❌ 无 | ✅ **Ollama 本地 LLM** 筛选摘要（0.971 准确率） | ❌ 无 |
| **配置化** | ✅ config.py + 环境变量 | ✅ CLI 参数 + 环境变量 | ✅ 环境变量 + `~/.fulltext_keys` + `fulltext-config` 交互脚本 + `PUBLISHER_TOOL_MAP` 运行时覆盖 | ✅ YAML 配置 | ✅ config.py |
| **导出格式** | BibTeX | ❌ 无 | JSON sidecar | BibTeX / RIS / CSV | Excel → Markdown |
| **NCPSSD 支持** | ✅ 关键词搜索 + 登录下载 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **微信支持** | ✅ 单篇下载 + 分享文本 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| **MCP 集成** | ❌ 无 | ❌ 无 | ✅ 原生 MCP Server | ❌ 无 | ❌ 无 |
| **回归测试** | ❌ 无 | ❌ 无 | ✅ 178 个标识符 benchmark | ✅ 基准测试脚本 | ❌ 无 |

### 1.2 NCPSSD 专项对比

| 维度 | **paper-harvester** | **paper-grab** (HuggingFace) | **translators_CN / Ncpssd.js** | **nssd-downloader** |
|------|---|---|---|---|
| **检索方式** | 关键词搜索（`/searchHandler/search`） | **ISSN 期刊检索**（`/journal/list` → `/journal/secure/details`） | 文章详情页 API（`/articleinfoHandler/getjournalarticletable`） | config.yml 配置期刊/年份/期号 |
| **API 端点** | `/searchHandler/search` | `/articleinfoHandler/getjournalarticletable` | `/articleinfoHandler/getjournalarticletable` | 网页爬虫 |
| **搜索字段** | IKTE/IKPYTE/IKST/IKET/IKSE 五字段OR | ISSN → 期刊页面 → articleIdList | 文章 ID → 元数据 | 期刊号遍历 |
| **ISSN 检索** | ❌ 无 | ✅ `search_by_issn()` | ✅ API 返回 `issn` 字段 | ❌ 无 |
| **PDF 下载** | 登录 → readurl → 解析下载链接 | ❌ 仅元数据（`url` 字段指向 readurl） | ✅ API 返回 `pdfurl` 字段直接获取 | ✅ Selenium + 验证码识别 |
| **DOI 提取** | ❌ 无 | ❌ 无（`doi: ""`） | ❌ 无 | ❌ 无 |
| **字段映射** | id→lngid, title, creator, cbw_name→journal, years→year, remark→abstract | titlec→title, mediac→journal, publishdate→date, showwriter→authors | titlec→title, mediac→journal, remarkc→abstract, vol→volume, num→issue, issn→ISSN | N/A |
| **古籍/集刊支持** | ❌ 无 | ❌ 无 | ✅ 古籍→book, 集刊→journalArticle | ❌ 无 |
| **Zotero 集成** | ❌ 无 | ❌ 无 | ✅ 原生 Zotero 翻译器 | ❌ 无 |

### 1.3 微信公众号文章下载对比

| 维度 | **paper-harvester** | **wechat-article-exporter** (ysrock) | **WeChat-Articles-Batch-Downloader** |
|------|---|---|---|
| **定位** | CLI 脚本（单篇+批量） | **在线 Web 应用**（Nuxt.js） | CLI 脚本 |
| **文章获取方式** | 已知 URL 直接抓取 | **公众号后台接口搜索全部文章列表** | Excel 输入 URL |
| **批量维度** | URL 列表文件 | **按公众号名批量导出全部文章** | Excel DOI/URL 列表 |
| **导出格式** | Markdown / HTML / JSON | HTML / JSON / Excel / TXT / **Markdown / Word** | Markdown / PDF |
| **样式还原** | Markdown（html2text 转换） | **HTML 100% 还原**（打包图片+样式） | Markdown |
| **图片处理** | 可选下载到本地 | HTML 打包 / 独立下载 | 下载到本地 |
| **阅读量/评论** | ❌ 无 | ✅ 阅读量/评论/转发量/点赞量 | ❌ 无 |
| **音视频** | ❌ 无 | ✅ 内嵌音视频导出 | ❌ 无 |
| **合集下载** | ❌ 无 | ✅ 按合集批量下载 | ❌ 无 |
| **分享文本提取** | ✅ `--share-text` | ❌ 无 | ❌ 无 |
| **部署方式** | Python CLI | Docker / Cloudflare Workers / 在线站 | Python CLI |
| **开放 API** | ❌ 无 | ✅ 开放 API 接口 | ❌ 无 |
| **订阅机制** | ❌ 无 | ✅ 按规则自动下载 | ❌ 无 |
| **credentials** | 无需 | 需抓包获取公众号后台 credentials | 无需 |

---

## 二、关键差距分析

### 2.1 🔴 降级链深度不足

**我们**：3 级（原始URL → Unpaywall → locked）
**DoiHunter**：6 级（Publisher → Unpaywall → OpenAlex → Europe PMC → arXiv → Legacy）
**fulltext-article-downloader**：per-publisher 定制，14+ 出版商，最深 6 级

**差距本质**：我们的降级链只覆盖了两个英文 OA 源，缺少 Europe PMC、OSTI、Semantic Scholar OA index、arXiv 等中间层。对于 NCPSSD 论文，降级链完全不适用（NCPSSD 走登录下载，不走 OA 路由）。

### 2.2 🔴 PDF 验证过于简单

**我们**：仅检查 `%PDF-` 前5字节
**fulltext-article-downloader**：`%PDF-` + **前两页文本提取 + Crossref 标题匹配** + SI heading 检测 + Elsevier body text 检测

**差距本质**：`%PDF-` 只能防止把 HTML 错误页存为 PDF，但无法检测以下情况：
- 首页 PDF（Elsevier 返回仅首页的" teaser"）
- Supporting Information 文件（携带文章标题但非正文）
- 错误的 OA 映射（DOI 被映射到无关文档）

### 2.3 🔴 缺少浏览器降级（反爬）

**我们**：纯 requests，无反爬应对
**Review Buddy**：Camoufox 浏览器（Elsevier 14%→90% 成功率）
**fulltext-article-downloader**：Chrome TLS 指纹（curl-cffi）

**差距本质**：Elsevier、Wiley、MDPI 等大出版商使用 Cloudflare 交互式挑战，纯 HTTP 客户端成功率极低（14%），浏览器降级可达 90%。不过这个差距对我们的**核心场景影响有限**——我们主要面向 NCPSSD（中文社科）和微信公众号，不涉及 Elsevier/Wiley。

### 2.4 🟡 NCPSSD 缺少 ISSN 期刊检索

**我们**：关键词搜索（`/searchHandler/search`）
**paper-grab**：ISSN → 期刊列表 → articleIdList → 逐篇元数据
**translators_CN**：文章详情 API（`/articleinfoHandler/getjournalarticletable`），API 直接返回 `pdfurl` 字段

**差距本质**：我们的关键词搜索能找到文章但不知道属于哪本期刊的全部文章。paper-grab 的 ISSN 路径可以按期刊批量检索。更关键的发现：**translators_CN 的 API 直接返回 `pdfurl` 字段**，这意味着我们可能不需要登录就能获取部分 PDF 下载链接——这与我们当前"必须登录才能下载"的假设可能不同。

### 2.5 🟡 微信只有"单篇下载"，缺"按公众号批量导出"

**我们**：已知 URL → Markdown
**wechat-article-exporter**：搜索公众号名 → 后台接口返回全部文章列表 → 批量导出

**差距本质**：这是**维度差异**而非深度差异。我们的 download_wechat.py 解决的是"已知 URL 的文章怎么保存"，wechat-article-exporter 解决的是"不知道 URL 怎么找到全部文章"。两个问题不同，且 wechat-article-exporter 的方案需要公众号后台 credentials（用户需抓包获取），门槛较高。

### 2.6 🟡 缺少 AI 筛选

**Review Buddy**：Ollama 本地 LLM 筛选摘要，0.971 准确率，3.7s/篇
**我们**：无

**差距本质**：Review Buddy 的 AI 筛选解决的是"5498 篇论文怎么缩减到相关子集"的问题。我们目前依赖 WorkBuddy Agent 本身做筛选（LLM 在对话中判断），方案不同但目标一致。

---

## 三、我们的独特优势（别人没有的）

| 能力 | paper-harvester | 最接近的竞品 |
|------|----------------|-------------|
| **NCPSSD 关键词搜索** | ✅ 五字段OR搜索 | paper-grab 只有 ISSN 检索 |
| **NCPSSD 登录+Cookie持久化** | ✅ 脚本内置登录 | nssd-downloader（2018，Python2.7，已过时） |
| **微信公众号单篇→Markdown** | ✅ 含分享文本提取 | wechat-article-exporter（Web应用，重） |
| **中英文统一数据模型** | ✅ Paper TypedDict + oa 三值语义 | 无竞品做此抽象 |
| **五维权重排序** | ✅ 相关性/被引/时效/来源/OA | 无竞品实现 |
| **WorkBuddy Skill 集成** | ✅ 原生 Skill | 无竞品 |
| **config.py 环境变量覆盖** | ✅ PH_* 环境变量 | DoiHunter 有类似设计 |

---

## 四、改进建议（按系统科学优先级重排）

### 🔴 P0：补全降级链 + PDF 深度验证

**参考**：DoiHunter 的 6 级降级链 + fulltext-article-downloader 的标题匹配验证

1. **扩展降级链**：原始URL → Unpaywall → Europe PMC → Semantic Scholar OA → arXiv → locked（从3级→6级）
2. **PDF 深度验证**：`%PDF-` 签名 → 提取前两页文本 → 检查标题关键词是否出现（不需要精确匹配，关键词存在即可）
3. **失败记录**：每篇下载结果附带 `source` + `error_type` + `note`

### 🟡 P1：NCPSSD ISSN 期刊检索

**参考**：paper-grab 的 `search_by_issn()` + translators_CN 的 `pdfurl` 字段

1. **新增 `search_by_issn(issn)` 函数**：`/journal/list?p=1&e=h%3D_ISSN:{issn}` → 解析 `ncpssd_params` → 获取 articleIdList → 逐篇调 API
2. **新增 `search_by_journal(name)` 函数**：先搜期刊名 → 获取 params → 检索文章
3. **检查 `pdfurl` 字段**：API 可能直接返回 PDF 下载链接，测试是否需要登录

### 🟡 P2：JSON Sidecar 元数据

**参考**：fulltext-article-downloader 的每篇 JSON 结果

每篇下载的论文附带 `{filename}.json`：
```json
{
  "title": "...",
  "authors": [...],
  "doi": "...",
  "source": "ncpssd | unpaywall | arxiv | ...",
  "downloaded_at": "2026-10-15T13:00:00",
  "oa_type": "true | ncpssd_login | false",
  "pdf_verified": true,
  "note": "preprint | accepted_manuscript | version_of_record"
}
```

### 🟢 P3：并发下载 + 限速

**参考**：fulltext-article-downloader 的 `workers` 参数 + 出版商限速

1. 在 `download_oa.py` 中增加 `--workers` 参数（默认1）
2. NCPSSD 下载保持单线程（平台限制），英文 OA 可并发
3. 限速：Unpaywall 1/s，Crossref polite pool，arXiv 3/s

### 🟢 P4：原子写入 + 中断处理

**参考**：DoiHunter 的 `.part` 文件 + 优雅 Ctrl+C

1. 下载先写入 `.part` 临时文件
2. PDF 验证通过后 `os.replace()` 原子移动到最终路径
3. 注册 signal handler，Ctrl+C 时清理 `.part` 文件

---

## 五、不需要跟进的方向（明确排除）

| 方向 | 排除理由 |
|------|----------|
| Camoufox 浏览器降级 | 我们面向 NCPSSD/微信，不涉及 Elsevier/Wiley Cloudflare 挑战 |
| MCP Server | paper-harvester 是 WorkBuddy Skill，WorkBuddy 本身就是 Agent，不需要额外 MCP 层 |
| Zotero 翻译器 | 不同生态，我们面向 WorkBuddy 对话流程 |
| Sci-Hub 集成 | 法律风险，明确排除 |
| 在线 Web UI（wechat-article-exporter） | 架构过重，且需要公众号后台 credentials，与我们的 CLI Skill 定位不符 |
| AI 摘要筛选（Review Buddy） | WorkBuddy Agent 本身已具备 LLM 筛选能力，无需内嵌 Ollama |

---

## 六、总结定位

```
                    中文社科论文          英文论文OA          微信公众号
                    ┌──────────┐       ┌──────────┐       ┌──────────┐
  fulltext-article  │          │       │★★★★★★  │       │          │
  downloader        │          │       │ (最完善)  │       │          │
                    ├──────────┤       ├──────────┤       ├──────────┤
  DoiHunter         │          │       │★★★★☆  │       │          │
                    ├──────────┤       ├──────────┤       ├──────────┤
  Review Buddy      │          │       │★★★★☆  │       │          │
                    ├──────────┤       ├──────────┤       ├──────────┤
  wechat-article    │          │       │          │       │★★★★★★  │
  exporter          │          │       │          │       │ (最完善)  │
                    ├──────────┤       ├──────────┤       ├──────────┤
  paper-grab        │★★★☆☆  │       │          │       │          │
                    ├──────────┤       ├──────────┤       ├──────────┤
  translators_CN    │★★★☆☆  │       │          │       │          │
                    ├──────────┤       ├──────────┤       ├──────────┤
  paper-harvester   │★★★★☆  │       │★★★☆☆  │       │★★★☆☆  │
  v0.2.0            │(关键词+  │       │(基础OA+  │       │(单篇+    │
                    │ 登录下载) │       │ 降级链)  │       │ 分享文本) │
                    └──────────┘       └──────────┘       └──────────┘
```

**核心结论**：paper-harvester 在三个维度都有可用能力，但在每个维度都不是最深。我们的**差异化竞争力**在于：
1. **唯一同时覆盖 NCPSSD + 微信 + 英文 OA 的工具**
2. **NCPSSD 关键词搜索 + 登录下载**（paper-grab 只有 ISSN，nssd-downloader 已过时）
3. **WorkBuddy Skill 原生集成**（对话式检索→下载→排序→BibTeX 全链路）

**最大短板**：英文 OA 降级链只有 3 级（竞品 6 级），PDF 验证只查 magic number（竞品做标题匹配）。但这两个短板对我们的核心场景（中文法学论文）影响有限——NCPSSD 论文不走 OA 路由，微信文章不走 PDF 验证。
