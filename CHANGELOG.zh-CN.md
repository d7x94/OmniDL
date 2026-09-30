[English](CHANGELOG.md) | [Tiếng Việt](CHANGELOG.vi.md) | 简体中文

# Changelog

本文件记录 OmniDL 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)。
版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

---

## [Unreleased]

暂无待发布的变更。

---

## v20.3.14 - 2026-09-30

### 修复

- "使用浏览器 Cookie"在 yt-dlp 和 gallery-dl 下载时现在读取"设置 > 网络"中所选的浏览器配置文件。此前 yt-dlp 读取最近使用的配置文件，gallery-dl 自行选择，下载可能使用其他账号（BUG-COOKIE-PROFILE-DL）
- Facebook Story 抓取在选择同一浏览器时使用设置中所选的配置文件启动浏览器；若该配置文件已不存在，会给出明确提示（BUG-COOKIE-PROFILE-DL）
- 浏览器扩展导出的 Cookie 文件保留 `#HttpOnly_` 行：TikTok 账号池不再把已登录的 Cookie 误判为"未登录"，TikTok/Facebook 直播的 FFmpeg 录制重新携带会话 Cookie（BUG-COOKIE-HTTPONLY）
- 快手下载只向快手自己的域名附加会话 Cookie。此前通过 Remote API 的下载可能把 Cookie 发送到 URL 中的任意域名（BUG-KS-COOKIE-HOST）
- 因崩溃中断的 Cookie 提取不再在 cookies 文件夹中永久留下完整的浏览器 Cookie（`_tmp_*`），下次启动时会被删除（BUG-COOKIE-TMP-JAR）
- Cookie 提取进行中时，同一 Cookie 栏的"选择"和"删除"按钮被禁用，提取结果不会覆盖刚选择的文件或重新登记刚清除的栏（BUG-COOKIE-EXTRACT-RACE）

---

## v20.3.13 - 2026-09-29

### 修复

- 设置、按平台 Cookie、CDP 在无法确定浏览器配置文件时会给出明确错误，而不再打开 `Default` 配置文件并保存其他账号的 Cookie。请先在浏览器框旁边的配置文件框中选择配置文件。全局 Cookie 的 CDP 按钮、TikTok 账号池以及 yt-dlp 按钮行为不变（BUG-CDP-NO-PROFILE）
- OK.ru 的按平台 Cookie 提取已可用。设置中提供了 OK.ru，但缺少 Cookie 域名列表，因此 CDP 和 yt-dlp 的每次提取都会过滤掉所有 Cookie 并报告"该平台没有 Cookie"（BUG-COOKIE-OKRU）

---

## v20.3.12 - 2026-09-29

### 修复

- 设置的网络页面中提取 Cookie 时，现在会读取你在浏览器选择框旁新增的"配置文件"框中选择的浏览器配置文件。此前按平台的 CDP 提取总是打开 `Default` 配置文件，导致 Instagram 拿到另一个账号的 Cookie。未知的配置文件、用显示名代替配置文件目录名、或端口已被其他浏览器会话占用时，现在都会给出明确的错误。CDP 提取失败时不再保留旧的 Cookie 文件并提示成功（BUG-CDP-PROFILE）

---

## v20.3.11 - 2026-09-29

### 修复

- Facebook 和 Instagram 图片帖子现在会在下载前显示真实的作者和标题，gallery-dl 的登录失败也会报告为登录错误，而不是"没有内容"。元数据探测读取的是 gallery-dl 从不输出的 `--dump-json` 格式，因此什么也找不到（BUG-GDL-DUMP）
- 以 `/photo/N` 结尾的 X 图片链接现在用 gallery-dl 下载，而不是重试三次后报 "Media #N is not a video"（BUG-X-PHOTO-N）
- 需要登录的 X 推文（NSFW 或受保护）立即失败并给出 cookie 提示，不再重试三次（BUG-X-AUTH）
- `t.co` 短链接现在使用 X 的 cookie 和限速（BUG-X-TCO）
- 点击取消现在会立即停止粘贴的 Instagram CDN 链接下载（BUG-IG-CDN-CANCEL）
- 下载 Facebook Story 时的网络错误不再留下被截断的视频，而是转到下一个捕获路径（BUG-FB-STORY-TRUNC）
- 网页界面的分析流和剪贴板分析路由现在会在没有可控浏览器的主机上拒绝 Facebook Story 和 waaw 链接，与 `/api/analyse` 一致（BUG-API-CDP-GUARD）
- Facebook 主页监控在取消或直播已结束后保留主页 URL，下次轮询不再报 "no username"（BUG-MON-URL）
- gallery-dl 调试日志不再打印代理密码和临时 cookie 路径（BUG-GDL-LOG-ARGV）
- 仅包含 "generate" 之类单词的 gallery-dl 错误不再被报告为限速（BUG-GDL-RATE-SUBSTR）
- 等待账号名额时被取消的 TikTok 任务现在会以已取消结束，而不是一直排队（BUG-TT-POOL-CANCEL）
- FFmpeg 停滞提示现在写明真实的 20 秒限制，而不是 120 秒（BUG-TT-STALL-TEXT）
- 分析阶段已找到视频格式的 Facebook 视频，若出现一次 "Cannot parse data" 会重试，而不是转交 gallery-dl（BUG-FB-PARSE-VIDEO）
- 错误信息中的 `Retry-After` 值上限为 120 秒，一个很大的值不再让工作线程停摆数小时（BUG-RETRY-AFTER-CAP）
- 主页标签在无法开始下载时（例如文件夹不可写）现在会显示错误，而不是静默失败（BUG-HOME-START-ERROR）

---

## v20.3.10 - 2026-09-28

### 修复

- 图片帖子从 yt-dlp 回退到 gallery-dl 后，清理步骤不再删除其他下载任务正在写入同一文件夹的文件；现在只删除以本任务 yt-dlp 所写媒体 ID 命名的文件（BUG-BU-SWEEP）
- 直播监控的录制任务在队列中被取消并保留部分文件（`PARTIAL_SAVED`）后，监控条目不再永远停在"录制中"；该条目会结束，账号监控继续（BUG-MON-PARTIAL）
- 获取 TikTok、Instagram 或 X 主页列表时，解密后的 Cookie 不再在 yt-dlp 读取之前被删除，因此私密主页会以登录状态读取，也不会再写回明文 Cookie 文件（BUG-PROFILE-COOKIE）
- 取消 gallery-dl 下载（Instagram、Facebook、X 图片）时会立即停止 gallery-dl，而不是等它下载完（BUG-GDL-CANCEL）
- 被取消或失败的 gallery-dl 下载不再在磁盘上留下解密后的会话 Cookie 文件（BUG-GDL-COOKIE-LEAK）
- gallery-dl 无法下载的 Instagram 帖子（需要登录、私密、限流）现在会报告失败，而不是显示完成却没有文件（BUG-GDL-IG-SILENT）
- 仅含图片的 X 帖子现在改用 gallery-dl 下载，不再重试 3 次后以 "No video could be found in this tweet" 失败（BUG-X-PHOTO）
- 向 Remote API 重复发送同一个 TikTok 短链接不再为同一场直播开始第二个录制（BUG-DUP-CANONICAL）

---

## v20.3.9 - 2026-09-28

### 修复

- 录制 Instagram 直播时，捕获到直播流 URL 后关闭浏览器不再在日志中留下两段 asyncio `CancelledError` 堆栈；现在会在 Playwright 关闭前移除直播流拦截路由（BUG-IG-ROUTE-TEARDOWN）
- Instagram 直播的 FFmpeg 出错时，日志现在完整保留最后 40 行错误输出，而不是只保留最后 600 个字符（那样会截掉说明真正错误原因的那一行）（BUG-IG-STDERR-TAIL）

---

## v20.3.7 - 2026-09-20

### 修复

- 含多张照片的 Facebook 帖子不再被错误下载成无关的广告视频，而是下载真正的照片（BUG-FB-ADVID）

### 新增

- 当 Facebook 帖子只能确认为广告、无法下载时，新增对应的错误提示（`err.fb_post_advert_only`）

---

## v20.3.6 - 2026-09-20

### 修复

- 含多张照片的 Facebook 帖子不再被错误解析成广告视频（BUG-FB-ADVID）
- Facebook 群组帖子（`facebook.com/groups/.../posts/...`）现在可以被识别并下载

---

## v20.3.5 - 2026-09-20

### 修复

- 含多张照片的 Facebook 帖子不再被下载成别人的广告视频（BUG-FB-ADVID）
- 解析失败的 Facebook 请求现在会出现在日志中，而不是不留任何痕迹

---

## v20.3.4 - 2026-09-20

### 修复

- TikTok：直播结束后，有时仍会在长达 30 分钟内被错误报告为受限/正在直播
- TikTok：单次限流错误（HTTP 429）不再重新启用一个实际已被 TikTok 封禁的检测方式

---

## v20.3.3 - 2026-09-19

### 修复

- 硬件编码器检测不再在 Windows 和 Linux 上浪费时间探测仅 macOS 支持的 VideoToolbox
- 硬件编码器检测失败时，错误提示更清晰

---

## v20.3.2 - 2026-09-17

### 修复

- 减少 `omnidl.log` 中的重复日志，让真正的错误和警告更容易被找到
- 修复一个在每次 TikTok 重试时都会重复出现的 yt-dlp 警告
- Taildrop 失败提示现在显示真实错误，而不是占位警告信息

---

## v20.3.1 - 2026-09-10

### 修复

- TikTok：已结束的直播在后续检查中仍可能被报告为正在直播
- TikTok：网络中断不再被误判为检测功能正常
- 通过 Taildrop 发送的 Facebook 相册不再被命名为 "Unknown"

---

## v20.3.0 - 2026-09-09

### 新增

- 识别更多 Facebook 照片/相册/帖子网址形式

### 修复

- 含图片的 Facebook 帖子现在可以下载（此前一律失败）
- 通过分享链接（`/share/p/...`）打开的 Facebook 帖子现在能正确解析并下载
- 图文视频混合的 Facebook 帖子现在会同时保存图片和视频，而不仅仅是图片

---

## v20.2.1 - 2026-09-09

### 变更

- 首次失败、重试后成功的下载任务不再被记录为错误

### 修复

- TikTok 下载有时会在成功完成后立即被重新检查并删除（数据丢失问题）
- 通过分享链接打开的 Facebook 图片帖子不再永久失败
- 无法恢复的 gallery-dl 下载失败不再被反复重试

---

## v20.2.0 - 2026-09-09

### 修复

- Facebook Story：无声视频有时被错误报告为有音频（Windows）
- Facebook Story 现在保存到任务指定的文件夹，而不是始终保存到默认下载文件夹
- Facebook Live 监控在长时间运行时不再出现内存泄漏
- Facebook Story 下载在超时后不再遗留未关闭的网络连接
- Facebook Story 音轨不再被错误判定为缺失
- `GET /api/ping` 返回的应用版本号已修正

---

## v20.1.0 - 2026-09-09

### 新增

- 下载 Facebook 图片帖子和相册（桌面应用和 Remote API）
- Facebook 相册现在保存到独立文件夹
- Live Monitor 可以监控 Facebook 主页和账号，开播时自动录制

---

## v20.0.0 - 2026-09-04

### 新增

- 支持一次转换多个文件，并可配置并行转换数量
- Remote API：新增批量文件转换接口
- 引擎和下载错误提示现已支持翻译（英语 / 越南语 / 中文）
- TikTok 账号池：每个浏览器 Profile 对应一个账号，接受前先做健康检查，支持安全重命名/刷新，自动清理已删除账号的 Cookie 文件
- 文档转换：Markdown、HTML 和 Office 文件与 PDF 互转（Documents 标签页 + Remote API）
- 压缩/解压功能：压缩为 ZIP/7z（可设密码），解压并预览压缩包
- 新增 waaw.ac 下载器
- 支持匿名下载粘贴的、已签名的 Instagram/Facebook CDN 链接
- TikTok 直播检测：在前三种检测方式同时被新的反爬策略击溃后，新增第四种检测方式

### 修复

- Facebook Story：浏览器已打开时提示更清晰，不再是含糊的连接超时
- Facebook Story：网速较慢时音频不再间歇性丢失
- Facebook Story：在 Linux 服务器上解析链接和下载的行为不再不一致
- 仅以 DASH（无 HLS）方式提供的 Facebook 直播现在能正确录制，不再被截断
- 已结束的 TikTok 直播可能在结束后数小时内被重新判定为正在直播
- TikTok 的限流冷却时间现在在所有账号间共享，不再按账号单独计算，减少无效请求
- 文件被锁定或正在使用时，现在会明确提示"文件正在使用"，而不是服务器错误
- 修复多个 Cookie/账号池问题：所有账号暂停时回退到共享 Cookie、Cookie 文件泄漏、账号池重建后账号状态过期
- 含越南语变音符号、中文或表情符号的文件名现在会原样发送给新设备，而不是一律转换成 ASCII（BUG-TD-NAME）
- 新增 Facebook 直播录制（此前只能下载到一小段片段，而非完整直播）
- 多项小修复：暂停/继续状态、队列与历史同步、Files 标签页、Live Monitor、Settings 标签页
- 压缩/解压标签页：密码显示/隐藏按钮不可见；"保留原文件名"选项现在能正常工作
- Facebook Story：Windows 上的命令行窗口闪现问题已彻底解决
- 各平台上直播录制的输出文件名已修正
- 修复无控制台窗口构建版本的启动崩溃问题，以及 Settings 中 "Verbose logging" 开关失灵的问题

### 安全

- 文档转换：阻止不可信 HTML/Markdown 内容访问本地文件和网络（路径限制、大小限制、并发数限制）
- 文件预览接口不再直接内联渲染 HTML/SVG 文件，防止存储型跨站脚本攻击（XSS）

---

## v19.0.0 - 2026-05-31

### 变更

- 桌面界面用 PySide6（Qt6）重写，采用全新视觉主题
- TikTok 直播检测重写为并发运行多种检测策略
- Remote App（PWA）界面已完全翻译为越南语
- 解密后的 Cookie 现在缓存在内存中，避免重复调用系统密钥环

### 新增

- TikTok 账号池：支持使用多个账号，自动按负载均衡
- 网络设置面板：为下载配置代理

### 修复

- TikTok 房间 ID 现在会通过 Remote API 传递，使备用检测策略能正常工作
- 通过请求限速降低了 TikTok Live 的限流错误

---

## v18.0.0 - 2026-04-30

### 新增

- Instagram 直播录制（仅限 Windows/macOS）
- 通过 Tailscale 以 HTTPS 方式访问 Remote API
- TikTok 账号直播检测（无需 Cookie）

### 修复

- TikTok Live 在多种情况下被错误报告为未直播（BUG-TT-08/09/10）
- Instagram 改变格式后，Instagram Live 改为使用 DASH 流（BUG-IG-01）
- Kuaishou：修复因临时网络错误和 Cookie 缺失导致的错误重新提取问题（BUG-KS-01/02）
- 修复转换失败后可能重复出现的 GPU 编码器卡死问题
- 修复下载 Facebook Story 时 Windows 上的命令行窗口闪现问题

---

## v16.3.1 - 2026-03-22

### 修复

- 修正侧边栏和示例配置文件中显示错误的应用版本号
- 启动时的依赖检查现在包含 Playwright

---

## v16.3.0 - 2026-03-21

### 移除

- 视频编辑器标签页（移除以让应用专注于下载功能）
- Threads 下载器（因 API 频繁变动、维护成本过高而移除）

### 修复

- 清理上述功能移除后遗留的代码

---

## v16.2.1 - 2026-03-21

### 修复

- Threads 下载器：修正了错误的 API 端点和应用 ID，并刷新了过期的身份验证令牌

---

## v16.2.0 - 2026-03-21

### 新增

- Threads 下载器（视频、图片、轮播帖子）
- macOS 支持 Facebook Story
- 视频编辑器新增分辨率选择（480p 至 4K）

### 修复

- 修复多个视频编辑器问题：崩溃、检测编码器时界面卡死、模糊效果错误、CRF 设置问题

---

## v16.1.0 - 2026-03-20

### 新增

- 特殊下载标签页，首个支持项为 Facebook Story

### 变更

- 重写 Facebook Story 引擎，改用浏览器自身的登录状态，替代原先自写的连接代码

### 修复

- 重复下载不再覆盖已有文件
- 长时间下载现在有 5 分钟的安全超时限制，不再无限挂起
- 下载完成后，操作按钮不再保持隐藏状态

---

## v16.0.0 - 2026-03-07

### 安全

- 修复 Cookie 文件处理中的路径遍历（path traversal）漏洞
- 移除"在文件资源管理器中显示"功能中的 shell 注入风险
- 用户数据（配置、历史记录、日志）从安装目录迁移到每个用户可写的独立位置
- 修复缩略图网址中的服务器端请求伪造（SSRF）风险
- Cookie 提取现在会根据允许列表校验浏览器名称
- 自定义 yt-dlp 参数现在会经过严格的允许列表过滤
- 更新依赖，其中包含 Pillow 的安全补丁

### 变更

- 配置和历史记录的写入现在是原子操作，降低数据损坏风险
- 历史记录存储改为仅追加（append-only）格式，提升可靠性
- 取消直播下载现在约 10 秒内响应，而不是最长 90 秒
