[English](README.md) | [Tiếng Việt](README.vi.md) | 简体中文

# OmniDL v20.3.29

一款桌面媒体下载工具，支持 YouTube、TikTok、Instagram、Twitter/X、Facebook 及 1000 多个网站——基于 Python、PySide6、yt-dlp 和 gallery-dl 构建。

## 主要功能

- **下载** 通过 yt-dlp 和 gallery-dl 从 1000 多个平台下载视频、音频和图片
- **批量下载** —— 粘贴多个网址；按平台设置延迟，避免触发限流
- **特殊下载** —— Facebook Story、Instagram 直播（通过 CDP，即浏览器调试接口）
- **Facebook 图片、相册和帖子** —— 图片帖子、相册和图文视频混合帖子，每个帖子一个文件夹。推荐和广告视频会被过滤
- **直播监控** —— 开播自动录制（Instagram 和 Facebook 需要 Cookie，TikTok 不需要）
- **转换** 用 FFmpeg 将已下载文件转换为 MP4、MP3、MKV、AVI。自动探测硬件编码器（NVENC/QSV/AMF/VideoToolbox），始终提供 CPU 方案作为备选
- **视频编辑器** —— 预览、剪辑、旋转、静音已下载文件
- **压缩/解压** —— 压缩文件为 7z/ZIP（可设密码），解压已有压缩包
- **文档转换** —— Markdown ↔ PDF、HTML ↔ PDF、Office ↔ PDF 互转（桌面标签页 + Remote API）；详见[文档转换](guides/document-conversion.zh-CN.md)
- **TikTok 账号池** —— 使用多个 TikTok Cookie，每个浏览器 Profile 一个，并做健康检查
- **多语言界面** —— 桌面应用和网页应用均支持英语、越南语、中文
- **按平台管理 Cookie** —— 支持浏览器、yt-dlp 或 CDP 导入（Brave/Chrome 127+）；加密存储
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
uv sync --group dev
```

需要 Remote API 支持：

```bash
uv sync --extra api --group dev
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

请妥善保管 API 令牌——任何持有它的人都能控制 OmniDL 并查看下载历史。不要将 8765 端口暴露到公网；使用 [Tailscale](guides/remote-iphone.zh-CN.md) 从局域网外安全访问。

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
uv sync --group dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

覆盖率门槛：`fail_under = 80` —— 不得降低。

CI 在每次 push/PR 时运行：`ruff check → mypy → pytest`（Python 3.11/3.12/3.13），并行运行安全检查任务 `bandit -ll → pip-audit`。

### 打包构建

推送 `v*.*.*` 标签时，CI（`.github/workflows/build.yml`）会自动构建发布版本：Windows 和 macOS 的 PyInstaller 构建，外加一个 Linux tarball，全部发布到 GitHub Release。

本地构建（Python 3.13）：

```bash
uv sync --extra build --group dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

请分发整个 `dist/OmniDL/` 文件夹，而不仅仅是可执行文件。

## 许可证

[MIT](LICENSE)

## 更多文档

- [通过 iPhone 远程控制（Remote API + Taildrop）](guides/remote-iphone.zh-CN.md)
- [文档转换](guides/document-conversion.zh-CN.md)
- [Remote API 端点](guides/api-endpoints.zh-CN.md)
- [Dependencies](guides/dependencies.zh-CN.md)

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

## Changelog

完整版本历史，包含每次 bug 修复的说明，见 [`CHANGELOG.md`](CHANGELOG.md)。
