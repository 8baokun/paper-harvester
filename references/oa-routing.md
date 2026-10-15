# OA 全文路由（合规）

## Tier 1：可直接自动下载
- 官方 OA 页面
- arXiv / bioRxiv / medRxiv / ChinaXiv
- OpenAlex `best_oa_location.pdf_url`
- Semantic Scholar `openAccessPdf.url`
- Unpaywall 解析出的作者自存 / 机构仓储 PDF
- PubScholar、NCPSSD 中明确标注“开放阅读/下载”的单篇
- PLoS / Frontiers / MDPI / Springer OA / SAGE OA / Hindawi 等出版商 OA

## Tier 2：跳转出版商，需点一次
- 出版商“Download PDF”按钮
- 需登录学校 WebVPN / CARSI 的期刊
- 需读者卡登录的国家图书馆 / 地方馆资源

## Tier 3：🔒 付费墙，只给链接不给文件
- 知网 / 万方 / 维普
- Elsevier ScienceDirect / Wiley / Taylor & Francis / Sage 非 OA
- Springer / Nature 非 OA
- 做法：输出 DOI 落地页 + 提示“走学校 WebVPN / CARSI / 图书馆文献传递”，**不尝试破解、不批量爆破登录**

## 禁止
- 默认走 Sci-Hub / LibGen / 其他盗版镜像
- 伪造 DOI、伪造引文
- 把搜索摘要说成“已读全文”
- 连续、系统、集中、批量下载或镜像 NCPSSD / 知网 / 万方等平台
- 用机器人绕过验证码或付费墙
