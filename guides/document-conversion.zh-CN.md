# 文档转换

**Documents** 标签页（以及 `POST /api/docs/convert`）在多种文档格式之间互转，支持 7 条转换路径：

| 源格式 | 目标格式 | 后端 |
|---|---|---|
| Markdown（`.md`、`.markdown` 等） | PDF | `markdown` + WeasyPrint |
| Markdown | HTML | `markdown` |
| HTML（`.html`、`.htm`、`.xhtml`） | PDF | WeasyPrint |
| PDF | Markdown | `pypdf` |
| PDF | HTML | `pypdf` |
| PDF | DOCX | LibreOffice |
| Office（`.docx`、`.xlsx`、`.pptx`、`.odt`、`.rtf`、`.csv` 等） | PDF | LibreOffice |

其中两个后端是**可选**的，缺少时 OmniDL 会优雅降级——`GET /api/docs/capabilities` 会报告主机支持哪些功能，桌面标签页会灰显不可用的部分，API 则返回 `503` 并给出清晰原因：

- **LibreOffice** —— 所有 Office 转换路径都需要它。未内置（安装包约 700 MB）；请从 [libreoffice.org](https://www.libreoffice.org/) 安装，标签页的 **Re-check** 按钮无需重启 OmniDL 即可识别。
- **Pango / GTK** —— WeasyPrint 在导入时加载 Pango、HarfBuzz 和 fontconfig。Linux 和 macOS 的包管理器自带这些库；**Windows** 上需安装 [GTK for Windows Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer) 才能使用 Markdown/HTML → PDF。

**不支持扫描版 PDF。** PDF → Markdown/HTML 只提取文本层；没有可选中文本的 PDF 会明确报错，而不是生成空文件。没有 OCR 功能。

**安全性。** 文档可能通过 `<img>`、`<link>`、`@import` 或 CSS `url()` 引用远程或任意本地资源。渲染器只允许 `data:` URI 以及源文档所在文件夹内的文件；其余一律拒绝、记录日志并跳过（页面仍会正常渲染，只是缺少该资源）。通过 Remote API 时，`source_path` 和 `out_dir` 均被限制在 `download_dir` 内，超过 200 MiB 的源文件会被拒绝，并发数上限为 2。
