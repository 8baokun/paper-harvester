---
name: paper-harvester
version: 0.4.0
description: 免费学术文献检索与 OA 全文定位、自动下载技能。用 OpenAlex/Crossref/Semantic Scholar/arXiv/NCPSSD 检索文献，自动去重、评级、下载开放获取（OA）PDF。支持国家哲学社会科学文献中心（NCPSSD）登录后自动下载中文论文 PDF，支持微信公众号文章正文提取并保存为 Markdown。v0.4 新增：8级多源降级链（原始URL→Unpaywall→Crossref→OpenAlex→Semantic Scholar→SSRN→EuropePMC→arXiv）、Semantic Scholar OA PDF 源（法学/合规类英文论文覆盖较好）、SSRN 法学预印本源、NCPSSD readDownloadUrl POST API 下载流程修正。
---

# Paper Harvester（论文收割机 · OA + NCPSSD + 微信公众号版）

当用户说：
- "帮我查 XX 的文献"
- "找最近 5 年关于 XX 的论文"
- "这批 DOI 里哪些能下全文 / 帮我下载"
- "写文献综述前先摸一遍"
- "把 OA 论文下载到本地"
- "从国家哲社文献中心下载论文"
- "查《法学研究》期刊的文章"
- "从微信公众号下载文章" / "保存这篇公众号文章"
- "把这篇微信文章转成 Markdown"

就按本 skill 执行。

## 总原则
1. 先免费开放源，后商业源。
2. 先元数据，再摘要，再 OA 全文。
3. **不伪造文献**；没检索到的就说没检索到。
4. **只自动下载 OA 论文和免费源论文**；付费墙论文一律只给合法访问路径，不尝试破解、不批量爆破登录。
5. 中文社科：NCPSSD（可登录下载）> PubScholar > 知网题录 > 学校图书馆。
6. 英文/跨学科：OpenAlex > Semantic Scholar > Crossref > arXiv/PubMed。
7. 法学预印本：SSRN（内网可能超时，外网可用）。
8. 微信公众号文章：直接 URL 提取正文，无需登录。
9. 下载限速：每篇间隔 1–3 秒，单源每分钟 ≤ 20 篇，UA 带邮箱，落盘校验 PDF 前缀。
10. **原子写入**：所有文件写入先写 `.part` 临时文件，成功后 `rename`，防止中断产生半截文件。
11. **优雅中断**：捕获 Ctrl+C 信号，保存已完成结果后退出。

## 执行流程
1. 把用户问题拆成 2–4 个检索式：
   - 主词 + 同义词（OR）
   - 时间窗（默认近 5 年，可覆盖经典文献）
   - 文献类型（期刊/综述/学位论文/预印本）
2. **判断检索范围**：
   - 中文社科 → 优先调 `scripts/search_ncpssd.py`
   - 按期刊名检索 → `scripts/search_ncpssd.py --journal "期刊名"`
   - 英文/跨学科 → 并行调 `scripts/search_openalex.py`、`scripts/search_semantic_scholar.py`、`scripts/search_crossref.py`
   - 预印本 → `scripts/search_arxiv.py`
   - 微信公众号文章 → `scripts/download_wechat.py`
3. 合并去重（按 DOI / 标题归一化）。
4. 对每篇判断下载路径：
   - **NCPSSD**（`oa: "ncpssd_login"`）：用户提供了账号 → `scripts/search_ncpssd.py --download`
   - **OA（OpenAlex/S2/arXiv）**（`oa: true` 且有 `pdf_url`）→ `scripts/download_oa.py`（8级降级链）
   - **微信公众号**：直接 URL → `scripts/download_wechat.py`（支持分享文本提取）
   - 否则 → 标 🔒 需机构权限
5. 按权重排序（`scripts/sort_papers.py`）：
   - 相关性 0.35 / 被引 0.25 / 时效 0.20 / 来源等级 0.10 / OA 可得性 0.10
6. 调 `scripts/build_bib.py` 生成 BibTeX（含本地 pdf 路径注释）。
7. 输出：
   - 文献表（标题/作者/年/期刊/被引/OA/链接）
   - 重点 10 篇一句话贡献
   - 已下载 PDF 清单（含本地路径 + JSON 元数据）
   - 🔒 需机构权限清单（DOI / 出版商页 / 学校通道提示）
   - BibTeX

## NCPSSD 自动下载（国家哲学社会科学文献中心）

### 能力
- **检索**：无需登录即可检索题录信息（标题/作者/期刊/年份/lngid）
- **期刊检索**（v0.3 新增）：`--journal "期刊名"` 按刊名精确检索
- **下载**：需用户提供 NCPSSD 账号（手机号/邮箱 + 密码），登录后自动下载 PDF
- **Cookie 持久化**：首次登录后保存 Cookie 到 `ncpssd_cookies.json`，后续免登录
- **JSON Sidecar**（v0.3 新增）：每篇下载的 PDF 自动附带 `.json` 元数据文件
- **原子写入**（v0.3 新增）：PDF 写入通过 `.part → rename`，防中断半截文件
- **失败记录**（v0.3 新增）：下载失败自动记录到 `failed_downloads.txt`
- **优雅中断**（v0.3 新增）：Ctrl+C 保存已完成结果
- **并发下载**（v0.3 新增）：`--parallel --workers N` 并发下载
- 注册地址：https://www.ncpssd.cn/register （免费注册，无验证码）

### 用法
```bash
# 仅检索（无需登录）
python scripts/search_ncpssd.py "业法财融合" 20

# 按期刊名检索（v0.3 新增）
python scripts/search_ncpssd.py --journal "法学研究" 20

# 检索 + 下载（需提供账号）
python scripts/search_ncpssd.py "业法财融合" 20 --download --user <手机号> --pass <密码>

# 检索 + 并发下载（v0.3 新增）
python scripts/search_ncpssd.py "法学理论" 10 --download --parallel --workers 3 --user <手机号> --pass <密码>

# 使用已保存的 Cookie 免登录下载
python scripts/search_ncpssd.py "法学理论" 10 --download --cookie ncpssd_cookies.json --user <手机号> --pass <密码>

# 从已有 JSON 结果下载
cat results.json | python scripts/search_ncpssd.py --stdin --download --user <手机号> --pass <密码>
```

### 下载策略（v0.4 增强）
- **首选方式（v0.4 新增）**：登录后 `POST /Literature/readDownloadUrl` (JSON body `{id: lngid}`) → 获取 `data.url` → 下载 PDF
- **后备方式**：访问文章阅读页 `/Literature/readurl?id={lngid}`
  - 若直接返回 PDF → 原子写入保存
  - 若返回 HTML → 解析页面中的 PDF 下载链接 → 下载
- 下载间隔 ≥ 2 秒/篇（可通过 `PH_MIN_GAP` 环境变量调整）
- **指数退避重试**：网络故障自动重试 3 次，退避因子 0.5
- **错误分类**：区分 404/403/超时/非PDF，区别处理
- 落盘校验前 5 字节 == `b"%PDF-"`
- **原子写入**：`.part` → `rename`，防中断半截文件
- **JSON Sidecar**：每篇 PDF 自动生成同名 `.json` 元数据
- **失败记录**：写入 `failed_downloads.txt`
- 文件命名：`{年}_{第一作者}_{标题前30字}.pdf`

## 微信公众号文章下载

### 能力（v0.3 增强）
- 从 `mp.weixin.qq.com/s/xxx` 链接提取文章正文
- **分享文本提取**：直接粘贴微信分享文本，自动提取 URL
- 自动提取标题、公众号名称、发布时间、摘要
- 转换为 Markdown 格式（使用 html2text）
- 可选下载文章中的图片到本地
- 支持批量下载（从文件读取 URL 列表）
- 支持同时保存 HTML / JSON 元数据
- **JSON Sidecar**（v0.3 新增）：每篇文章自动生成 `_meta.json` 元数据
- **原子写入**（v0.3 新增）：所有文件写入通过 `.part → rename`
- **优雅中断**（v0.3 新增）：批量下载时 Ctrl+C 保存已完成结果

### 用法
```bash
# 下载单篇文章为 Markdown
python scripts/download_wechat.py "https://mp.weixin.qq.com/s/xxxxx"

# 从分享文本提取 URL 并下载
python scripts/download_wechat.py --share-text "我在微信读过这篇文章：标题 https://mp.weixin.qq.com/s/xxxxx"

# 指定输出目录 + 下载图片 + 保存 HTML
python scripts/download_wechat.py "https://mp.weixin.qq.com/s/xxxxx" -o ./articles --html --download-images

# 批量下载（并发）
python scripts/download_wechat.py --input urls.txt -o ./articles --download-images --workers 3
```

### 输出结构
```
wechat_articles/
└── 文章标题/
    ├── 文章标题.md           # Markdown 正文
    ├── 文章标题_meta.json    # 元数据（v0.3 自动生成）
    ├── 文章标题.html         # HTML（可选）
    ├── 文章标题_info.json    # 详细元数据（--json 可选）
    └── images/               # 图片目录（可选）
        ├── image_001.jpg
        └── ...
```

## OA 下载策略（v0.4 — 8级多源降级链）
- **oa 字段三值语义**：`True`（真OA）/ `"ncpssd_login"`（条件OA，需登录）/ `False`（非OA）
- **8级降级链**：
  1. 原始 PDF URL（如有 `oa: true` 且有 `pdf`）
  2. Unpaywall API（查 DOI 的 OA PDF）
  3. Crossref API（查全文链接）
  4. OpenAlex API（查 OA PDF URL）
  5. **Semantic Scholar API**（v0.4 新增 — 有 `openAccessPdf` 字段，法学/合规类英文论文覆盖较好，限流严格需退避）
  6. **SSRN**（v0.4 新增 — 法学预印本主阵地，内网可能超时但外网可用）
  7. Europe PMC（生物医学领域 OA 全文）
  8. arXiv（预印本，按标题搜索或 DOI 含 arxiv）
  9. → 标记 locked
- **PDF深度验证**（v0.3 新增）：
  - `%PDF-` 签名校验
  - PyMuPDF 提取前两页文本，与标题做关键词匹配（匹配率 ≥ 30%）
  - PyMuPDF 不可用时降级为正则提取 PDF 文本流
  - 防 HTML 页面被伪装为 PDF 或下载到错误文件
- **指数退避重试**：网络故障自动重试 3 次（退避因子 0.5）
- **错误分类**：404/403（换源）、429/5xx/超时（重试）、非PDF（换源）
- **原子写入**：`.part` → `rename`
- **JSON Sidecar**：每篇 PDF 自动生成 `.json` 元数据
- **失败记录**：写入 `failed_downloads.txt`
- **并发下载**：`--parallel --workers N`（最大 4 并发）
- **优雅中断**：Ctrl+C 保存已完成结果
- 下载前发 `User-Agent` + 邮箱标识
- 限速：每篇间隔 1–3 秒，单源每分钟 ≤ 20 篇（可通过 `PH_MIN_GAP`/`PH_MAX_PER_MIN` 调整）
- OA=false → **不尝试破解**；输出：
  `🔒 需机构权限：<doi 落地页>`
  `建议：学校 WebVPN / CARSI / 图书馆文献传递`

## 配置化
所有硬编码参数已提取到 `scripts/config.py`，支持环境变量覆盖：
- `PH_UA`：User-Agent
- `PH_EMAIL`：联系邮箱（用于 API UA）
- `PH_TIMEOUT` / `PH_DL_TIMEOUT`：请求/下载超时
- `PH_MIN_GAP` / `PH_MAX_PER_MIN`：下载限速
- `PH_MAX_RETRIES` / `PH_BACKOFF`：重试次数和退避因子
- `PH_OUT_DIR` / `PH_WECHAT_OUT`：输出目录
- `PH_COOKIE_FILE`：NCPSSD Cookie 文件路径
- `PH_S2_API_KEY`：Semantic Scholar API key（可选，提高限额）

## 统一数据模型
所有检索脚本输出的文献数据遵循 `scripts/models.py` 定义的 `Paper` TypedDict schema。
`oa` 字段三值语义确保检索→排序→下载链路无语义漂移。

## 文献排序
`scripts/sort_papers.py` 实现 SKILL.md 定义五维权重排序：
```bash
# 对检索结果按五维权重排序
cat results.json | python scripts/sort_papers.py "检索关键词"

# 取前 10 篇
cat results.json | python scripts/sort_papers.py "检索关键词" --limit 10
```

## OA 路由
见 `references/oa-routing.md`。禁止默认走 Sci-Hub。

## 中文源说明
见 `references/chinese-sources.md`。

## 依赖安装
```bash
pip install requests beautifulsoup4 html2text PyMuPDF
```

## 输出格式
### 文献候选（前 15）
| # | 标题 | 作者 | 年 | 来源 | 被引 | OA | 链接 |
### 已下载 PDF
- ...（每篇附带 `.json` 元数据）
### 🔒 需机构权限
- ...
### BibTeX
- 写入 `lit.bib`
