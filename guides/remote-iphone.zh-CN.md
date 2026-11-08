# 通过 iPhone 远程控制（Remote API + Taildrop）

通过 Tailscale 从 iPhone 控制 OmniDL，并将下载完成的文件直接发送到手机。这两个功能可以独立使用，但搭配使用效果更好。

**前提条件**

- 电脑和 iPhone 都已安装 Tailscale —— [tailscale.com](https://tailscale.com)
- 两台设备登录同一个 Tailscale 账号（同一个 tailnet）
- 已安装 Remote API 依赖组：`uv sync --extra api --group dev`

## 1. 用 Tailscale 连接两台设备

在两台设备上安装 Tailscale，用同一账号登录，确认电脑出现在 iPhone 的 Tailscale 应用中。记下电脑的 MagicDNS 名称（Tailscale 为设备自动分配的主机名，例如 `my-laptop`）——即使 IP 变化，这个名称也保持不变。

## 2. 在 OmniDL 中启用 Remote API

打开 **Settings → Remote API** 并开启。OmniDL 会生成随机 API 令牌，保存在系统密钥环中（Windows 为 Credential Manager，macOS 为 Keychain，绝不写入配置文件）。服务器在 `http://0.0.0.0:8765` 启动，监听所有网络接口，包括 Tailscale。

所有请求都需要 `X-API-Token` 请求头。未经 Tailscale 保护时，不要将 8765 端口暴露到公网。

## 3. 在 iPhone 上打开应用

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

## 4. 设置 Taildrop 接收文件

Taildrop 在 Tailscale 设备之间点对点发送文件——不经过云端。

在 **Settings → Taildrop** 中开启功能，点击 **Detect nodes** 查找在线的 Tailscale 设备，在列表中勾选你的 iPhone（可多选）。选择发送模式：

- **Ask**（默认）—— 仅在你手动点击 "Send" 时才发送文件
- **Always** —— 每个下载完成后自动发送

在 iPhone 上，每个到达的文件都会弹出 Taildrop 通知；点击通知，选择 **Save**，文件即保存到 Files 应用。文件名中的表情符号或特殊字符会在发送前自动清理。

你也可以从桌面端 Queue 标签页（**Send** 按钮）或 Remote App 的队列中手动触发发送。

## 典型流程

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

## Windows 开机自动启动

为 `OmniDL.exe` 创建快捷方式，按 `Win + R`，输入 `shell:startup`，把快捷方式放进该文件夹。OmniDL 将随 Windows 启动，若 Remote API / Taildrop 此前已开启，则会自动就绪。
