# Remote API Endpoints

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
