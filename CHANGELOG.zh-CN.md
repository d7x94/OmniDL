[English](CHANGELOG.md) | [Tiếng Việt](CHANGELOG.vi.md) | 简体中文

# Changelog

本文件记录 OmniDL 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)。
版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

---

## [Unreleased]

暂无待发布的变更。

---

## v20.3.29 - 2026-11-07

### 已修复

- Taildrop：文件名以点开头的文件（上传者和标题为空时会得到 ". - date - ..."）到达 iPhone 时成为隐藏文件，看起来像什么都没收到。现在发送到手机的文件名会去掉开头的点、空格和连字符

---

## v20.3.28 - 2026-11-07

### 已修复

- Web UI：页面重新加载时（例如在手机上切换到其他应用后），Batch 标签页不再丢失链接和分析结果。列表和文本框会被恢复；仍在分析中的行会回到待处理状态

---

## v20.3.27 - 2026-11-05

### 已变更

- CI：测试运行现在包含此前被跳过的 `tests/test_e2e.py` 和 `tests/test_logger_and_manager_extra.py`。下载管理器测试现在会把关每一次推送

---

## v20.3.26 - 2026-10-20

### 已变更

- Instagram Live：在直播 DASH 流上 FFmpeg 报 "Unable to read to manifest" 失败时，调试日志现在会记录 Instagram CDN 对单次 manifest 请求的响应情况（状态、大小、传输编码、是否完整），以查找此故障的原因

---

## v20.3.25 - 2026-10-19

### 已修复

- History：Re-download、Copy URL、Open folder、Rename 和 Delete 按钮不再截断标签文字
- Convert：Clear 和 Subtitles 按钮不再截断标签文字
- Home：Browse 按钮不再截断标签文字
- 顶栏的主题按钮不再截断标签文字

---

## v20.3.24 - 2026-10-15

### 已修复

- History：Clear all 现在会清空历史记录，而不是在确认后静默失败
- History：部分保存的下载会显示已翻译的 "Partial" 徽章，并且可以筛选
- History：当历史文件无法写入时，删除或清空条目会让它们继续保留在列表中，而不是在重启后又出现
- Live Monitor：检查被监控的 Facebook 主页后，不再让它一直停留在检查中状态

---

## v20.3.23 - 2026-10-14

### 已修复

- Instagram：主页直播检查会在报告结果之前删除临时解密的 Cookie
- CI：测试套件在所有测试通过后不再于退出时使解释器崩溃（退出码 139）
- CI：某个测试不再在本次运行的剩余部分用桩替换 UI 模块，该问题曾导致之后的一个 Convert 标签页测试中止
- CI：TikTok 直播检查器的测试单独运行时可以通过

---

## v20.3.22 - 2026-10-13

### 已修复

- Batch：Retry 按钮上的计数会在删除行、提交失败以及开始新的分析时更新
- Batch：重试成功的行会重新显示已勾选的复选框，与 Queue All 提交的内容一致
- Batch：所有条目都失败的顺序 Queue All 运行现在会显示错误提示和状态，而不是 "done"
- Batch：顺序运行期间 Retry 和 Analyse 会被锁定，Queue All 在最后一个条目完成之前保持锁定
- Batch：在最后一行正在分析时删除它，会让标签页回到空闲状态
- Batch：切换语言会刷新行的删除提示和 URL 数量
- Remote API：`POST /api/download/batch` 在排队大批量任务时不再阻塞事件循环

---

## v20.3.21 - 2026-10-09

### 已修复

- History：主题切换时，卡片和 Clear 按钮现在会重新应用样式，而不是保留旧颜色
- History：Rename 按钮的检查会将相对文件名按下载文件夹解析，因此文件夹（gallery-dl）行不再显示 Rename

---

## v20.3.20 - 2026-10-08

### 已修复

- 主窗口：最小宽度现在为 1290，因此在窗口最小尺寸下使用越南语时，顶栏标签页不再重叠
- Editor：侧边面板的各行（文字叠加选项、颜色和效果滑块）会自动换行，因此在 380 px 的面板中不会有控件被截断

---

## v20.3.19 - 2026-10-06

### 已修复

- Settings：在 Settings 中切换主题后，顶栏主题按钮的文字会同步更新
- Settings：TikTok 账号 Cookie 刷新完成后不再一直停留在"刷新中"状态
- Settings：Tailscale HTTPS 重置后 API 重启失败时会回滚 HTTPS 并刷新令牌标签，与启用流程一致
- Settings：Global CDP 提取遇到不支持的浏览器时，先显示错误而不是先弹出确认框
- Settings：滑块数值（如并发下载数）每次变化立即保存，不会因 500 毫秒内关闭应用而丢失
- Settings：Remote API 开关、轮换令牌和 Tailscale HTTPS 控件在其中一个操作运行时会被锁定，避免两个操作重叠
- Settings：切换语言或主题时会等待 Settings 的后台任务结束后再重建页面，避免较晚结束的任务重新启用新一轮的按钮

---

## v20.3.18 - 2026-10-05

### 已修复

- Home 标签页：切换主题后会重绘欢迎页、结果卡片、画质卡片和徽章，不再保留旧主题下难以阅读的颜色
- Home 标签页：上一个条目仍在加载的缩略图不再出现在没有缩略图的结果上
- Home 标签页：直播结果会清除上一次的状态行（图片提示或错误文本）
- Home 标签页：Browse 和 Add to queue 按钮的提示文字会随界面语言切换
- Remote API：POST /api/analyse 和 /api/clipboard/analyse 在客户端断开或请求超时时会取消分析线程

---

## v20.3.17 - 2026-10-03

### 已修复

- Special Download: 第二次下载不再使打开文件夹 / 预览按钮失效；新任务开始时会清除上一次的结果
- 队列页的清除在未安装可选 API 组件 (fastapi) 时不再报错
- 历史页的全部清除不再删除仍有 Remote Convert 任务运行的条目
- 分析流：客户端中途断开不再丢弃仍在运行的任务，重连不会启动重复提取
- Remote API 清除已完成项时，仅报告真正被排除的队列任务
- Remote API 删除文件时同步清除对应的历史记录，历史页不再对已删除文件提供预览 / 发送 / 转换
- 编辑器页在标签页隐藏时切换语言，面板显示/隐藏按钮的文字保持正确

---

## v20.3.16 - 2026-10-02

### 修复

- 分析按钮的过期回调（来自已被替代的请求）不再清空新分析的取消按钮/加载动画状态
- 设置 > 清除数据 现在也会停止正在运行的 Remote API 服务器、停止 Tailscale Serve，并重建 TikTok 账号池，而不是让它们继续指向已删除的配置
- Remote API 开关（API 服务器、Tailscale HTTPS、轮换令牌）在操作期间加锁，重启失败时自动回滚，不再卡在误导性状态
- Editor 标签页的导出与预览不再互相抢占；预览加载时开始导出会被拒绝，而不是同时损坏两者
- Queue 标签页的清除按钮不再误删仍有活跃 Remote Convert 任务的条目
- Home 标签页通过 toast 显示分析错误（此前某些路径下是静默的），浏览/加入队列按钮和 LIVE 徽章已翻译并添加提示
- 特殊下载的 Facebook Story 预检（检测浏览器是否在运行）不再阻塞界面线程长达 5 秒——现在在后台线程运行
- TikTok cookie 刷新不再允许在上一次提取未完成时二次点击启动重复提取
- 下载后多文件转换（gallery-dl 批量）现在跟踪还有多少文件在转换，不再在第一个完成时就把按钮切到"完成"；自定义编码器面板的 GPU 探测不再阻塞控件构建
- Batch 标签页在分析中删除条目不再把分析计数拉到 -1 并卡住加载动画；运行批次时"重试"按钮已禁用
- Convert 标签页的文件卡片现在会随主题切换重新上色，不再停留在创建时的主题；whisper 支持探测在批次中途返回时不再提前启用字幕按钮；文件选择对话框的过滤文字已翻译
- Live Monitor 不再直接导入 infrastructure/ 来检查 cookie（BUG-LM-INFRA-IMPORT）；记录回到等待状态时会重置 URL（BUG-MON-URL）；操作按钮已添加提示；错误行上的"立即检查"会重新执行与"添加 URL"相同的校验
- 通知面板的标题、"全部清除"按钮和逐条关闭提示现已翻译，并随语言切换更新
- 分析缓存的容量上限不再仅因某个 SSE 任务最旧就将其正在进行的任务驱逐，此前这会破坏该 URL 的请求去重
- 3 个遗漏的文件修改路由（queue-file 删除的 gallery-dl 分支、files/delete、files/rename）现在遇到 Windows 文件锁定（AUDIT-02）时返回 409 而非 500
- `GET /api/queue/{task_id}/file` 现在拒绝内联提供 HTML/SVG/XML，与 `GET /api/files/serve` 的存储型 XSS 防护保持一致
- `GET /api/convert/{job_id}/file` 现在通过一次原子快照读取任务状态和文件名，不再分开读取未加锁的属性
- Taildrop 传输在早期失败（文件缺失、无有效目标节点，或服务正在关闭）时现在会为每个目标节点上报失败事件，而不是让客户端的传输指示器永远卡住、没有终止信号
- `DELETE /api/history/{task_id}` 不再在将历史记录写入磁盘时阻塞事件循环
- 文件夹删除部分失败（`DELETE /api/files/delete`）现在仍会为已成功删除的文件清除队列/历史记录，不再让它们全部指向一个基本已消失的路径

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

## v20.3.8 - 2026-09-23

### 修复

- Remote App：分析过程中手机连接中断并重新连接时，同一链接不再从头重新分析，而是等待正在进行的任务结果
- Taildrop：因文件名以外的原因发送失败时（例如 `502 Bad Gateway`，无法连接 iPhone），不再以 ASCII 文件名重新上传整个文件

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

Older versions (v20.2.1 and earlier): [guides/CHANGELOG_ARCHIVE.zh-CN.md](guides/CHANGELOG_ARCHIVE.zh-CN.md).
