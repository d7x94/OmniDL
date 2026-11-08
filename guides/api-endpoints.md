# Remote API Endpoints

When `api_enabled=True`, the server runs at `http://0.0.0.0:8765`. Every request needs the `X-API-Token` header.

| Method | Path | Description |
|---|---|---|
| GET | `/api/ping` | Health check — returns the app version and the `facebook_story` capability flag (Story capture needs a local Brave/Chrome, so it is `true` only on Windows and macOS) |
| POST | `/api/analyse` | Analyze a URL, return MediaInfo |
| GET | `/api/analyse/stream` | Analyze a URL over SSE (live progress) |
| POST | `/api/clipboard/analyse` | Analyze the URL currently on the clipboard |
| POST | `/api/download` | Start a download |
| POST | `/api/download/batch` | Enqueue up to 500 downloads in one call (Batch tab) |
| GET | `/api/queue` | List all tasks |
| GET | `/api/queue/{task_id}` | Get one task's status |
| POST | `/api/queue/{task_id}/pause` | Pause a task |
| POST | `/api/queue/{task_id}/resume` | Resume a task |
| POST | `/api/queue/{task_id}/cancel` | Cancel a task |
| DELETE | `/api/queue/items` | Remove tasks from the queue by id |
| DELETE | `/api/queue/finished` | Clear completed/failed tasks from the queue |
| POST | `/api/queue/{task_id}/transfer` | Send a file via Taildrop |
| POST | `/api/queue/{task_id}/rename` | Rename the output file of a completed task |
| DELETE | `/api/queue/{task_id}/file` | Delete a file from disk |
| GET | `/api/queue/{task_id}/file` | Stream/preview a file (Range requests) |
| GET | `/api/queue/{task_id}/fileinfo` | File metadata (name, size, existence) |
| GET | `/api/convert/encoders` | List available GPU/CPU encoders |
| GET | `/api/convert/codecs` | List output codecs + subtitle support this FFmpeg build has |
| GET | `/api/convert/concurrency` | Current parallel-conversion limit and the server maximum |
| POST | `/api/convert/concurrency` | Set how many conversions run at once (1-8, persisted) |
| POST | `/api/queue/{task_id}/convert` | Start a convert job |
| POST | `/api/queue/{task_id}/subtitles` | Generate subtitles (.srt) only, no re-encode |
| GET | `/api/convert/{job_id}` | Get a convert job's status |
| POST | `/api/convert/{job_id}/cancel` | Cancel a convert job |
| GET | `/api/convert/{job_id}/file` | Stream a converted file (iOS Safari); `?kind=srt` for the subtitle sidecar |
| DELETE | `/api/convert/{job_id}/file` | Delete a converted file |
| GET | `/api/history/stats` | Download history stats |
| GET | `/api/history` | Download history |
| DELETE | `/api/history/{task_id}` | Delete one history entry |
| GET | `/api/files/browse` | Browse files/folders on the computer |
| POST | `/api/files/convert` | Convert an arbitrary file (outside the queue) |
| POST | `/api/files/convert/batch` | Queue up to 100 files with one set of settings |
| POST | `/api/files/subtitles` | Generate subtitles (.srt) for an arbitrary file |
| DELETE | `/api/files/delete` | Delete a file by path |
| GET | `/api/files/serve` | Stream a file by absolute path |
| POST | `/api/files/transfer` | Send a file by path via Taildrop |
| GET | `/api/nodes` | List configured Taildrop nodes |
| POST | `/api/archive/compress` | Compress files to 7z/ZIP (optional password) |
| POST | `/api/archive/extract` | Extract an archive |
| POST | `/api/archive/contents` | Preview an archive's contents before extracting |
| GET | `/api/docs/capabilities` | Report which document-conversion back-ends the host has |
| POST | `/api/docs/convert` | Convert a document (Markdown/HTML/Office ↔ PDF) |
| GET | `/api/monitor` | List watched live streams |
| POST | `/api/monitor` | Add a profile/URL to the live monitor (TikTok, Instagram, Facebook page/profile, or a direct live URL) |
| POST | `/api/monitor/interval` | Set the live-check interval |
| DELETE | `/api/monitor/{item_id}` | Stop watching |
| POST | `/api/monitor/{item_id}/cancel` | Stop recording, keep watching |
| POST | `/api/monitor/{item_id}/pause` | Pause live checks for one link |
| POST | `/api/monitor/{item_id}/resume` | Resume live checks for one link |
| POST | `/api/monitor/{item_id}/check-now` | Check live status immediately |
| POST | `/api/monitor/pause` | Pause the whole live monitor |
| POST | `/api/monitor/resume` | Resume the whole live monitor |
| GET | `/api/settings/language` | Current UI language + supported languages |
| POST | `/api/settings/language` | Set the UI language (`en` / `vi` / `zh`) |
| GET | `/api/events` | SSE stream — real-time progress/status |
| GET | `/` | PWA (iPhone web app) |
