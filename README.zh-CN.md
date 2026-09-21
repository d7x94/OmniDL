[English](README.md) | [Tiếng Việt](README.vi.md) | 简体中文

# OmniDL v20.3.7

一款桌面媒体下载工具，支持 YouTube、TikTok、Instagram、Twitter/X、Facebook 及 1000 多个网站——基于 Python、PySide6、yt-dlp 和 gallery-dl 构建。

## 主要功能

- **下载** 通过 yt-dlp 和 gallery-dl 从 1000 多个平台下载视频、音频和图片
- **批量下载** —— 粘贴多个网址；按平台设置延迟，避免触发限流
- **特殊下载** —— Facebook Story、Instagram 直播（通过 CDP，即 Chrome DevTools Protocol，Playwright 驱动的浏览器调试接口）
- **Facebook 图片、相册和帖子** —— 图片帖子、相册以及图文视频混合帖子由 gallery-dl 和 yt-dlp 协同下载，每个帖子保存到独立文件夹；Facebook 在帖子页面插入的推荐/广告视频会被过滤，确保图片帖子不会被错误下载成别人的广告
- **直播监控** —— 开播自动录制；可监控 Instagram 账号（需要 Cookie）、TikTok 账号（无需 Cookie）或 Facebook 主页/账号（需要 Cookie）
- **转换** 用 FFmpeg 将已下载文件转换为 MP4、MP3、MKV、AVI。自动探测硬件编码器（NVENC/QSV/AMF/VideoToolbox），始终提供 CPU 方案作为备选
- **视频编辑器** —— 预览、剪辑、旋转、静音已下载文件
- **压缩/解压** —— 压缩文件为 7z/ZIP（可设密码），解压已有压缩包
- **文档转换** —— Markdown ↔ PDF、HTML ↔ PDF、Office ↔ PDF 互转（桌面标签页 + Remote API）；详见[文档转换](#文档转换)
- **TikTok 账号池** —— 添加多个 TikTok Cookie，每个浏览器 Profile 对应一个；每个 Cookie 接受前都会做健康检查，可原地重命名或刷新
- **多语言界面** —— 桌面应用和 PWA 均支持英语、越南语、中文；引擎和下载错误信息也会翻译
- **按平台管理 Cookie** —— 支持浏览器导入、yt-dlp 提取或 CDP 提取（Brave/Chrome 127+）；静态加密存储；切换提取方式时自动清理孤立 Cookie
- **Taildrop 文件传输** —— 将文件发送到 iPhone 或任意 Tailscale 节点，支持多设备
- **下载后操作** —— 直接从队列中转换、通过 Taildrop 发送或删除
- **Remote API**（可选）—— 通过内置 PWA 从 iPhone 或局域网内任意浏览器控制 OmniDL

## 系统要求

- Python 3.11 及以上
- Windows、macOS 或 Linux
- 发布版本已内置 FFmpeg；若从源码运行，需将 FFmpeg 加入 `PATH` 才能使用转换/编辑器/文档功能
- [Tailscale](https://tailscale.com) —— 仅在需要从本机以外访问 Remote API 时需要

## 安装

```bash
uv sync --extra dev
```

需要 Remote API 支持：

```bash
uv sync --extra api --extra dev
```

## 快速开始

### 桌面端

```bash
uv run python main.py
```

### Remote UI / Web API

1. 在桌面应用中打开 **Settings → Remote API** 并开启。OmniDL 会生成 API 令牌并保存在系统密钥环中（绝不写入配置文件）。
2. 服务器在 `http://0.0.0.0:8765` 启动。所有请求都需要 `X-API-Token` 请求头。
3. 在同一网络下的浏览器中打开 `http://<电脑名或IP>:8765` 并输入令牌。

请妥善保管 API 令牌——任何持有它的人都能控制 OmniDL 并查看下载历史。不要将 8765 端口暴露到公网；使用 [Tailscale](#通过-iphone-远程控制remote-api--taildrop) 从局域网外安全访问。

## 基本配置

配置在桌面端 **Settings** 标签页中修改（下载文件夹、最大并发下载数、重试次数、主题、语言、Cookie 文件、Remote API、Taildrop）。配置保存在[数据目录](#数据目录)下的 `config.json` 中——该文件由应用自动写入，不建议手动编辑。

主要配置项：

| 配置项 | 位置 | 作用 |
|---|---|---|
| 下载文件夹 | Settings → File Location | 已完成下载的保存位置 |
| 最大并发下载数 | Settings → Download Behaviour | 同时运行的下载数量 |
| 界面语言 | Settings → Appearance | `en` / `vi` / `zh` |
| Cookie 文件 | Settings → Network | 按平台设置的 Cookie，用于需要登录的内容 |
| Remote API | Settings → Remote API | 启用/禁用远程控制服务器及令牌 |

## 常见问题

| 现象 | 原因 | 解决方法 |
|---|---|---|
| Safari/浏览器无法打开 `http://my-laptop:8765` | Tailscale 未开启，或两台设备账号不同 | 检查 Tailscale 应用，确认在同一个 tailnet |
| Remote App 中出现 "Unauthorized" | 令牌错误或缺失 | Settings → Remote API → 复制令牌并重新输入 |
| iPhone 未出现在 Detect nodes 中 | iPhone 的 Tailscale 未开启或离线 | 打开 iPhone 上的 Tailscale，确认已连接 |
| Taildrop 报错 `400 Bad Request` | 文件名含特殊字符 | OmniDL 会先尝试发送完整 Unicode 文件名，失败后自动重试转换为 ASCII 的名称；若仍失败，请更新 iPhone 上的 Tailscale |
| 文件已发送但没有通知 | iOS 未开启 Tailscale 通知 | iPhone Settings → Notifications → Tailscale → 允许 |
| Remote App 变慢 / SSE（Server-Sent Events，用于实时进度推送）中断 | Tailscale 连接不稳定 | 在 Tailscale 设置中切换为中继模式 |

## 目录结构

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, account_pool, config, history.
ui/              PySide6 tabs and widgets. Consumes app/service layer only.
utils/           Pure helpers — ffmpeg_locator, logger, live checkers, tiktok_detection/.
tests/           pytest unit tests. Mock-only — no real network or subprocess.
```

依赖方向：`ui -> app -> domain`，`infrastructure -> domain`，`utils` 可在任何层使用。

## 开发与测试

```bash
uv sync --extra dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

覆盖率门槛：`fail_under = 80` —— 不得降低。

CI 在每次 push/PR 时运行：`ruff check → mypy → pytest`（Python 3.11/3.12/3.13），并行运行安全检查任务 `bandit -ll → pip-audit`。

### 打包构建

推送 `v*.*.*` 标签时，CI（`.github/workflows/build.yml`）会自动构建发布版本：Windows 和 macOS 的 PyInstaller 构建，外加一个 Linux tarball，全部发布到 GitHub Release。

本地构建（Python 3.13）：

```bash
uv sync --extra build --extra dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

请分发整个 `dist/OmniDL/` 文件夹，而不仅仅是可执行文件。

## 许可证

[MIT](LICENSE)

---

## 通过 iPhone 远程控制（Remote API + Taildrop）

通过 Tailscale 从 iPhone 控制 OmniDL，并将下载完成的文件直接发送到手机。这两个功能可以独立使用，但搭配使用效果更好。

**前提条件**

- 电脑和 iPhone 都已安装 Tailscale —— [tailscale.com](https://tailscale.com)
- 两台设备登录同一个 Tailscale 账号（同一个 tailnet）
- 已安装 Remote API 依赖组：`uv sync --extra api --extra dev`

### 1. 用 Tailscale 连接两台设备

在两台设备上安装 Tailscale，用同一账号登录，确认电脑出现在 iPhone 的 Tailscale 应用中。记下电脑的 MagicDNS 名称（Tailscale 为设备自动分配的主机名，例如 `my-laptop`）——即使 IP 变化，这个名称也保持不变。

### 2. 在 OmniDL 中启用 Remote API

打开 **Settings → Remote API** 并开启。OmniDL 会生成随机 API 令牌，保存在系统密钥环中（Windows 为 Credential Manager，macOS 为 Keychain，绝不写入配置文件）。服务器在 `http://0.0.0.0:8765` 启动，监听所有网络接口，包括 Tailscale。

所有请求都需要 `X-API-Token` 请求头。未经 Tailscale 保护时，不要将 8765 端口暴露到公网。

### 3. 在 iPhone 上打开应用

在 Safari 中访问 `http://<电脑名>:8765`（例如 `http://my-laptop:8765`），按提示输入 API 令牌。使用分享 → "添加到主屏幕" 可像原生应用一样安装。

如果无法连接：确认两台设备的 Tailscale 都已开启，或尝试使用 Tailscale IP 而非名称（例如 `http://100.64.0.5:8765`）。

| 功能 | 作用 |
|---|---|
| 粘贴网址 + Analyse | 解析链接，选择格式/画质 |
| Download | 开始下载，实时查看进度 |
| Queue | 查看所有任务，暂停/继续/取消 |
| Convert | 将已下载文件转换为适合 iPhone 的 MP4 |
| Preview | 在浏览器内预览视频/图片 |
| Send（Taildrop） | 通过 Tailscale 将文件发送到 iPhone |
| Delete | 从电脑中删除文件 |

### 4. 设置 Taildrop 接收文件

Taildrop 在 Tailscale 设备之间点对点发送文件——不经过云端。

在 **Settings → Taildrop** 中开启功能，点击 **Detect nodes** 查找在线的 Tailscale 设备，在列表中勾选你的 iPhone（可多选）。选择发送模式：

- **Ask**（默认）—— 仅在你手动点击 "Send" 时才发送文件
- **Always** —— 每个下载完成后自动发送

在 iPhone 上，每个到达的文件都会弹出 Taildrop 通知；点击通知，选择 **Save**，文件即保存到 Files 应用。文件名中的表情符号或特殊字符会在发送前自动清理。

你也可以从桌面端 Queue 标签页（**Send** 按钮）或 Remote App 的队列中手动触发发送。

### 典型流程

```
iPhone (Safari/PWA)                     Computer (OmniDL)
──────────────────────────────────────────────────────────
1. Open Remote App, paste a TikTok link
2. Tap Analyse                     →   Analyzes the URL
3. Pick MP4, tap Download          →   Starts downloading
4. Watch live progress             ←   SSE events
5. Tap Convert                     →   FFmpeg converts to iPhone MP4
6. Tap Send                        →   Tailscale sends the file
7. Notification appears            ←   File arrives
8. Tap Save in Files app
9. Tap Delete in Remote App        →   File removed from the computer
```

### Windows 开机自动启动

为 `OmniDL.exe` 创建快捷方式，按 `Win + R`，输入 `shell:startup`，把快捷方式放进该文件夹。OmniDL 将随 Windows 启动，若 Remote API / Taildrop 此前已开启，则会自动就绪。

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| pyside6 | >=6.7 | GUI framework |
| yt-dlp | >=2026.7.4 | Download engine (video / live) |
| gallery-dl | >=1.32.12 | Image/gallery download engine (Instagram photos, Facebook photos/albums, Twitter images) |
| requests | >=2.31.0 | HTTP client |
| packaging | >=23.0 | Version utilities |
| playwright | >=1.40 | Facebook Story + Instagram Live CDP via `connect_over_cdp()` |
| cryptography | >=41.0.0 | Cookie at-rest encryption (macOS Fernet/AES-128-CBC) |
| keyring | >=24.0.0 | macOS Keychain key storage; Brave/Chrome 127+ App-Bound cookie decrypt |
| Pillow | >=10.3.0 | Thumbnail decoding/resizing, frame effects |
| platformdirs | >=4.0.0 | Platform-appropriate data directory resolution |
| PySocks | >=1.7.1 | SOCKS proxy support for yt-dlp/gallery-dl |
| curl-cffi | >=0.16.0 | Chrome TLS impersonation for TikTok/Kuaishou requests |
| loguru | >=0.7.3 | Application logging |
| ffmpeg-python | >=0.2.0 | FFmpeg command construction |
| py7zr | >=1.1.3 | 7z archive compression/extraction |
| pyzipper | >=0.4.0 | Password-protected ZIP compression/extraction |
| weasyprint | >=69.0 | HTML → PDF rendering for the Documents tab |
| markdown | >=3.6 | Markdown → HTML for the Documents tab |
| pypdf | >=4.2.0 | PDF text extraction (PDF → Markdown/HTML) |

**可选 —— Remote API**（仅当 `api_enabled=True` 时需要，通过 `uv sync --extra api` 安装）：

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for the remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as a daemon thread alongside the Qt event loop |

开发/构建依赖位于 `pyproject.toml` 的 `dev` 和 `build` 可选依赖组中（`uv sync --extra dev`）。

## 文档转换

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

## 数据目录

| 操作系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\OmniDL\` |
| macOS | `~/Library/Application Support/OmniDL/` |
| Linux | `~/.local/share/OmniDL/` |

Cookie 文件保存在 `<data_dir>/cookies/`，静态加密存储（Windows 上为 DPAPI，即 Windows 内置的按用户加密机制；macOS 上为 Fernet）。
日志写入系统日志目录（Windows 为 `%LOCALAPPDATA%\OmniDL\Logs\`，
macOS 为 `~/Library/Logs/OmniDL/`，Linux 为 `~/.local/state/OmniDL/log/`）：
`omnidl.log` 始终以 INFO 级别记录，开启 Settings 中的 *Verbose logging* 后还会生成 DEBUG 级别的 `omnidl_debug.log`。两者都在 5 MB 时轮转，最多保留 3 个文件。

## Remote API Endpoints

当 `api_enabled=True` 时，服务器运行在 `http://0.0.0.0:8765`。所有请求都需要 `X-API-Token` 请求头。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ping` | 健康检查——返回应用版本及 `facebook_story` 能力标志（Story 抓取需要本机安装 Brave/Chrome，因此仅在 Windows 和 macOS 上为 `true`） |
| POST | `/api/analyse` | 解析网址，返回 MediaInfo |
| GET | `/api/analyse/stream` | 通过 SSE 解析网址（实时进度） |
| POST | `/api/clipboard/analyse` | 解析剪贴板中当前的网址 |
| POST | `/api/download` | 开始下载 |
| POST | `/api/download/batch` | 一次调用最多加入 500 个下载任务（Batch 标签页） |
| GET | `/api/queue` | 列出所有任务 |
| GET | `/api/queue/{task_id}` | 获取单个任务状态 |
| POST | `/api/queue/{task_id}/pause` | 暂停任务 |
| POST | `/api/queue/{task_id}/resume` | 继续任务 |
| POST | `/api/queue/{task_id}/cancel` | 取消任务 |
| DELETE | `/api/queue/items` | 按 id 从队列中移除任务 |
| DELETE | `/api/queue/finished` | 清除已完成/失败的任务 |
| POST | `/api/queue/{task_id}/transfer` | 通过 Taildrop 发送文件 |
| POST | `/api/queue/{task_id}/rename` | 重命名已完成任务的输出文件 |
| DELETE | `/api/queue/{task_id}/file` | 从磁盘删除文件 |
| GET | `/api/queue/{task_id}/file` | 流式播放/预览文件（支持 Range 请求） |
| GET | `/api/queue/{task_id}/fileinfo` | 文件元数据（名称、大小、是否存在） |
| GET | `/api/convert/encoders` | 列出可用的 GPU/CPU 编码器 |
| GET | `/api/convert/codecs` | 列出当前 FFmpeg 支持的输出编码及字幕支持情况 |
| GET | `/api/convert/concurrency` | 当前并行转换上限及服务器最大值 |
| POST | `/api/convert/concurrency` | 设置同时运行的转换数量（1-8，持久化保存） |
| POST | `/api/queue/{task_id}/convert` | 开始一个转换任务 |
| POST | `/api/queue/{task_id}/subtitles` | 仅生成字幕（.srt），不重新编码 |
| GET | `/api/convert/{job_id}` | 获取转换任务状态 |
| POST | `/api/convert/{job_id}/cancel` | 取消转换任务 |
| GET | `/api/convert/{job_id}/file` | 流式播放已转换文件（iOS Safari）；`?kind=srt` 获取配套字幕文件 |
| DELETE | `/api/convert/{job_id}/file` | 删除已转换文件 |
| GET | `/api/history/stats` | 下载历史统计 |
| GET | `/api/history` | 下载历史 |
| DELETE | `/api/history/{task_id}` | 删除一条历史记录 |
| GET | `/api/files/browse` | 浏览电脑上的文件/文件夹 |
| POST | `/api/files/convert` | 转换任意文件（不在队列内） |
| POST | `/api/files/convert/batch` | 用同一组设置批量转换最多 100 个文件 |
| POST | `/api/files/subtitles` | 为任意文件生成字幕（.srt） |
| DELETE | `/api/files/delete` | 按路径删除文件 |
| GET | `/api/files/serve` | 按绝对路径流式传输文件 |
| POST | `/api/files/transfer` | 按路径通过 Taildrop 发送文件 |
| GET | `/api/nodes` | 列出已配置的 Taildrop 节点 |
| POST | `/api/archive/compress` | 压缩文件为 7z/ZIP（可设密码） |
| POST | `/api/archive/extract` | 解压一个压缩包 |
| POST | `/api/archive/contents` | 解压前预览压缩包内容 |
| GET | `/api/docs/capabilities` | 报告主机支持哪些文档转换后端 |
| POST | `/api/docs/convert` | 转换文档（Markdown/HTML/Office ↔ PDF） |
| GET | `/api/monitor` | 列出正在监控的直播 |
| POST | `/api/monitor` | 添加监控的账号/网址（TikTok、Instagram、Facebook 主页/账号，或直接的直播网址） |
| POST | `/api/monitor/interval` | 设置直播检查间隔 |
| DELETE | `/api/monitor/{item_id}` | 停止监控 |
| POST | `/api/monitor/{item_id}/cancel` | 停止录制，继续监控 |
| POST | `/api/monitor/{item_id}/pause` | 暂停某个链接的直播检查 |
| POST | `/api/monitor/{item_id}/resume` | 恢复某个链接的直播检查 |
| POST | `/api/monitor/{item_id}/check-now` | 立即检查直播状态 |
| POST | `/api/monitor/pause` | 暂停整个直播监控 |
| POST | `/api/monitor/resume` | 恢复整个直播监控 |
| GET | `/api/settings/language` | 当前界面语言 + 支持的语言列表 |
| POST | `/api/settings/language` | 设置界面语言（`en` / `vi` / `zh`） |
| GET | `/api/events` | SSE 流——实时进度/状态 |
| GET | `/` | PWA（iPhone 网页应用） |

## Changelog

完整版本历史，包含每次 bug 修复的说明，见 [`CHANGELOG.md`](CHANGELOG.md)。
