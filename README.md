# OmniDL v17.1.0 (post-release)

A desktop media downloader supporting YouTube, TikTok, Instagram, Twitter/X, Facebook, and 1000+ sites — built with Python, CustomTkinter, yt-dlp, and gallery-dl.

## Features

- **Download** video, audio, and images from 1000+ platforms via yt-dlp and gallery-dl
- **Convert** downloaded files to MP4, MP3, MKV, AVI using FFmpeg (GPU-accelerated where available)
- **Live stream monitor** — auto-record when a stream goes live; Instagram profile watch (cookie-based) and TikTok profile watch (no cookie needed)
- **Batch download** — paste multiple URLs; per-platform analysis delays prevent rate-limiting
- **Remote API** (optional, FastAPI + uvicorn) — control OmniDL from iPhone via built-in PWA at `http://localhost:8765`
- **Per-platform cookie management** — browse, yt-dlp extraction, or CDP extraction (Brave/Chrome 127+); DPAPI/Fernet at-rest encryption; orphan cleanup on method switch
- **Multi-device Taildrop** file transfer via Tailscale CLI — send files directly to iPhone or any Tailscale node
- **Post-download actions** — convert, send via Taildrop, or delete directly from the download queue

## Architecture

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, config, history. Side effects here.
ui/              CustomTkinter tabs and widgets. Consumes app/service layer only.
utils/           Pure helpers — ffmpeg_locator, deno_locator, helpers, logger,
                 instagram_live_checker, tiktok_live_checker. No omnidl imports.
tests/           pytest unit tests. Mock-only — no real network or subprocess.
```

**Layer rule:** `ui` → `app` → `domain`. `infrastructure` → `domain`. `utils` imported by all. `ui` NEVER imports `infrastructure` directly.

---

## Remote API + Taildrop — Gửi file về iPhone qua Tailscale

OmniDL có thể điều khiển từ xa qua iPhone và tự động gửi file về iPhone thông qua Tailscale. Hai tính năng này hoạt động độc lập nhưng kết hợp với nhau tốt nhất.

### Yêu cầu

- **Tailscale** cài trên cả máy tính và iPhone — [tailscale.com](https://tailscale.com)
- Cả hai thiết bị cùng một Tailscale account (hoặc cùng tailnet)
- OmniDL cài phụ thuộc Remote API: `uv sync --extra api --extra dev`

---

### Bước 1 — Bật Tailscale trên máy tính và iPhone

1. Cài Tailscale trên máy tính (Windows/macOS) và trên iPhone từ App Store.
2. Đăng nhập cùng một tài khoản Tailscale trên cả hai thiết bị.
3. Xác nhận hai thiết bị thấy nhau: mở Tailscale app trên iPhone, kiểm tra máy tính xuất hiện trong danh sách. Ghi lại tên MagicDNS của máy tính (ví dụ: `my-laptop`) và tên MagicDNS của iPhone (ví dụ: `iphone-12-pro-max`).

> **Lưu ý:** Tailscale MagicDNS cho phép dùng tên thiết bị thay cho IP. Tên này ổn định ngay cả khi IP thay đổi.

---

### Bước 2 — Bật Remote API trong OmniDL

1. Mở OmniDL → **Settings** → cuộn xuống mục **⚡ REMOTE API**.
2. Bật toggle **Enable Remote API**.
3. OmniDL tự tạo một API token ngẫu nhiên và lưu vào keyring của hệ thống. Token hiển thị ngay trong Settings.
4. Server khởi động trên `http://0.0.0.0:8765` — lắng nghe trên tất cả interface, bao gồm Tailscale interface.

> **Bảo mật:** API token bắt buộc cho mọi request. Token được lưu trong Windows Credential Manager hoặc macOS Keychain — không ghi vào file config. Không expose port 8765 ra internet nếu không dùng Tailscale.

---

### Bước 3 — Truy cập Remote App từ iPhone

1. Trên iPhone, mở Safari và truy cập:
   ```
   http://<ten-may-tinh>:8765
   ```
   Ví dụ: `http://my-laptop:8765`

2. Trang PWA (Progressive Web App) của OmniDL mở ra. Nhập API token khi được hỏi.

3. **Thêm vào màn hình chính** để dùng như app thật: Safari → nút Share → "Add to Home Screen". Sau đó mở từ icon như app bình thường.

> **Nếu không kết nối được:** Kiểm tra Tailscale đang bật trên cả hai thiết bị. Thử dùng Tailscale IP thay cho tên (ví dụ `http://100.64.0.5:8765`). IP Tailscale xem trong Tailscale app.

#### Tính năng trong Remote App (iPhone)

| Tính năng | Mô tả |
|---|---|
| Dán URL + Analyse | Phân tích link, chọn format/chất lượng |
| Download | Bắt đầu tải, xem tiến trình real-time |
| Queue | Xem toàn bộ hàng chờ, pause/resume/cancel |
| Convert | Convert file đã tải sang MP4 tối ưu cho iPhone |
| Preview | Xem trước video/ảnh ngay trong trình duyệt |
| Send (Taildrop) | Gửi file về bộ nhớ iPhone qua Tailscale |
| Delete | Xóa file khỏi ổ cứng máy tính sau khi đã lưu |

---

### Bước 4 — Cấu hình Taildrop để gửi file về iPhone

Taildrop là tính năng của Tailscale cho phép gửi file giữa các thiết bị trong cùng tailnet — không qua cloud, trực tiếp peer-to-peer.

#### 4a. Bật Taildrop trong OmniDL

1. Mở **Settings** → mục **📱 TAILDROP**.
2. Bật toggle **Enable Taildrop**.
3. Click **Detect nodes** để OmniDL tự tìm các thiết bị Tailscale đang online.
4. Tích chọn iPhone của bạn trong danh sách (ví dụ: `iphone-12-pro-max`). Có thể chọn nhiều thiết bị nếu muốn gửi đồng thời.
5. Chọn **Send mode**:
   - **Ask (mặc định):** File chỉ được gửi khi bạn nhấn nút "Send" thủ công trong queue hoặc Remote App. Phù hợp nếu không muốn tự động gửi tất cả.
   - **Always:** Tự động gửi file ngay sau khi tải xong. Phù hợp nếu muốn mọi file đều được chuyển sang iPhone.

#### 4b. Nhận file trên iPhone

- File gửi qua Taildrop sẽ xuất hiện dưới dạng notification trên iPhone.
- Tap vào notification → chọn **Save** → file lưu vào **Files app** của iOS (thư mục Tailscale).
- Từ Files app có thể mở trực tiếp hoặc copy sang Photos/thư mục khác.

> **Lưu ý tên file:** Tailscale iOS từ chối file có tên chứa emoji hoặc ký tự đặc biệt (trả về lỗi 400). OmniDL tự động sanitize tên file trước khi gửi — bạn không cần làm gì thêm.

#### 4c. Gửi file thủ công (Send mode = Ask)

**Từ desktop:** Trong Queue tab, nhấn nút **Gửi** (Send) trên item đã tải xong.

**Từ iPhone (Remote App):** Trong queue, chọn file đã tải xong → nhấn **Send** → file được gửi ngay về iPhone.

---

### Luồng sử dụng điển hình

```
iPhone (Safari/PWA)                    Máy tính (OmniDL)
─────────────────────────────────────────────────────────
1. Mở Remote App, dán link TikTok
2. Nhấn Analyse                   →   Phân tích URL
3. Chọn format MP4, nhấn Download →   Bắt đầu tải
4. Xem progress real-time         ←   SSE events
5. Tải xong → nhấn Convert        →   FFmpeg chuyển sang MP4 iPhone-compatible
6. Convert xong → nhấn Send       →   Tailscale file cp → iphone
7. Nhận notification trên iPhone  ←   File đến
8. Tap Save → lưu vào Files app
9. Nhấn Delete trong Remote App   →   Xóa file khỏi ổ cứng máy tính
```

---

### Chạy tự động khi khởi động (Windows)

Để OmniDL luôn sẵn sàng nhận lệnh từ iPhone mà không cần mở thủ công:

1. Tạo shortcut của `OmniDL.exe`.
2. Nhấn `Win + R` → gõ `shell:startup` → OK.
3. Copy shortcut vào thư mục Startup vừa mở.

OmniDL sẽ tự khởi động cùng Windows. Remote API và Taildrop hoạt động ngay khi app mở (nếu đã bật trong Settings trước đó).

---

### Xử lý sự cố

| Triệu chứng | Nguyên nhân | Giải pháp |
|---|---|---|
| Safari không load được `http://my-laptop:8765` | Tailscale chưa bật hoặc hai thiết bị khác account | Kiểm tra Tailscale app, đảm bảo cùng tailnet |
| "Unauthorized" trong Remote App | Token sai hoặc chưa nhập | Vào Settings → Remote API → copy token, nhập lại |
| Không thấy iPhone trong Detect nodes | iPhone chưa bật Tailscale hoặc offline | Mở Tailscale trên iPhone, đảm bảo connected |
| Taildrop không gửi được (`400 Bad Request`) | Tên file chứa ký tự lạ | OmniDL tự xử lý — nếu vẫn lỗi, kiểm tra version Tailscale trên iPhone |
| File gửi xong nhưng không thấy notification | iOS notification bị tắt cho Tailscale | Settings iPhone → Notifications → Tailscale → bật Allow Notifications |
| Remote App load chậm / SSE ngắt | Kết nối Tailscale không ổn định | Đổi về Tailscale relay mode trong Tailscale Settings |

---

## Setup (Development)

```bash
uv sync --extra dev
```

With Remote API support:

```bash
uv sync --extra api --extra dev
```

## Running

```bash
uv run python main.py
```

## Testing

```bash
uv run pytest --cov=. --cov-report=term-missing
```

Coverage threshold: `fail_under = 80` — must not be lowered.

## Building (Windows / macOS EXE)

Builds are automated via the CI/CD pipeline (`.github/workflows/build.yml`).
Pushing a `v*.*.*` tag triggers PyInstaller builds for Windows and macOS and automatically publishes a GitHub Release with both binaries attached.

To build locally (requires Python 3.13 and the dev dependencies):

```bash
uv sync --extra build --extra dev
# Output: dist\OmniDL\OmniDL.exe  (Windows)
#         dist/OmniDL/OmniDL      (macOS bundle via ditto)
```

Distribute the entire `dist\OmniDL\` folder, not just the `.exe`.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| customtkinter | >=5.2.2 | GUI framework |
| yt-dlp | >=2025.1.1 | Download engine (video / live) |
| gallery-dl | >=1.27.0 | Image/gallery download engine (Instagram photos, Twitter images) |
| Pillow | >=10.3.0 | Thumbnail rendering (patches CVE-2024-28219) |
| requests | >=2.31.0 | HTTP client |
| packaging | >=23.0 | Version utilities |
| platformdirs | >=4.0.0 | Platform-appropriate user-data directories (SEC-3) |
| PySocks | >=1.7.1 | SOCKS4/5 proxy support for yt-dlp |
| playwright | >=1.40 | Facebook Story CDP via `connect_over_cdp()` |
| cryptography | >=41.0.0 | Cookie at-rest encryption (macOS Fernet/AES-128-CBC) |
| keyring | >=24.0.0 | macOS Keychain key storage; Brave/Chrome 127+ App-Bound cookie decrypt |

**Optional — Remote API** (only needed when `api_enabled=True`, included via `uv sync --extra api`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as daemon thread alongside Tkinter |

Dev/build deps are in the `dev` and `build` optional groups in `pyproject.toml` (`uv sync --extra dev`).

## Data Directory

| OS | Path |
|---|---|
| Windows | `%APPDATA%\OmniDL\` |
| macOS | `~/Library/Application Support/OmniDL/` |
| Linux | `~/.local/share/OmniDL/` |

Cookie files are stored in `<data_dir>/cookies/` and encrypted at rest (DPAPI on Windows, Fernet on macOS).
Logs are written to `<data_dir>/logs/omnidl_run.log` with 5 MB rotation, 3 backups.

## Remote API Endpoints

Khi `api_enabled=True`, server chạy tại `http://0.0.0.0:8765`. Mọi request cần header `X-API-Token: <token>`.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/ping` | Health check |
| POST | `/api/analyse` | Phân tích URL, trả về MediaInfo |
| POST | `/api/download` | Bắt đầu tải |
| GET | `/api/queue` | Danh sách tất cả tasks |
| GET | `/api/queue/{task_id}` | Trạng thái một task |
| POST | `/api/queue/{task_id}/pause` | Pause task |
| POST | `/api/queue/{task_id}/resume` | Resume task |
| POST | `/api/queue/{task_id}/cancel` | Cancel task |
| DELETE | `/api/queue/finished` | Xóa completed/failed khỏi queue |
| POST | `/api/queue/{task_id}/transfer` | Gửi file qua Taildrop |
| DELETE | `/api/queue/{task_id}/file` | Xóa file khỏi ổ cứng |
| GET | `/api/queue/{task_id}/file` | Stream/preview file (Range requests) |
| GET | `/api/queue/{task_id}/fileinfo` | Metadata file (tên, dung lượng, tồn tại) |
| GET | `/api/convert/encoders` | Danh sách encoder GPU/CPU khả dụng |
| POST | `/api/queue/{task_id}/convert` | Bắt đầu convert (RemoteConvertService) |
| GET | `/api/convert/{job_id}` | Trạng thái convert job |
| POST | `/api/convert/{job_id}/cancel` | Cancel convert |
| GET | `/api/convert/{job_id}/file` | Stream file đã convert (iOS Safari) |
| DELETE | `/api/convert/{job_id}/file` | Xóa file convert |
| GET | `/api/events` | SSE stream — real-time progress/status |
| GET | `/api/history` | Lịch sử tải |
| GET | `/` | PWA (HTML app cho iPhone) |

## CI Pipeline

Two parallel GitHub Actions jobs on every push/PR:

**Job 1 — `test`** (Python 3.11, 3.12, 3.13 matrix):
```
ruff check → mypy → pytest (--cov, fail_under=80)
```

**Job 2 — `security`** (Python 3.13):
```
bandit -ll → pip-audit
```

**Build job** (triggered by `v*.*.*` tags only):
```
PyInstaller (Python 3.13) → GitHub Release
```

## Changelog Highlights

### Post-v17.1.0 (current)
- **BUG BV**: TikTok live stream với tên chứa emoji không còn crash ffmpeg trên Windows (`0xCBAE0008`) — `restrictfilenames=True` được áp dụng cho live downloads trên Windows
- **BUG BW**: FAILED và CANCELLED tasks không còn hiển thị nút pause/cancel bị disabled — các nút này được ẩn hoàn toàn khi task đạt terminal state
- **BUG BT**: Pause/cancel buttons unconditionally hidden on COMPLETED status regardless of `task.filename`
- **BUG BU**: Audio-only output formats (mp3, m4a, flac, aac, opus, wav, ogg) now correctly use `FFmpegExtractAudio` postprocessor instead of `merge_output_format` — fixes "Postprocessing: Conversion failed!" on YouTube/other sites where native format is webm/opus
- **TikTok Live Monitor**: `LiveMonitorTab` now supports TikTok profile watch (`@username` URLs); no cookie required; `utils/tiktok_live_checker.py` added

### v17.1.0
- Per-platform cookie security hardening: browse handlers encrypt immediately (BUG BM), clear deletes physical files (BUG BN), all 6 acquisition handlers clean up orphaned files on method switch (BUG BO)
- TikTok VOD format selector: 4-tier watermark-free selector applied to both format_id branches (BUG BP/BS); `remote_components` fixed to list (BUG BQ); format audit logging (BUG BR)

### v17.0.0
- `PostDownloadActions` component (convert/send/delete bar after successful download)
- `TaildropService.send_file_to_nodes()` multi-device concurrent send
- Multi-device checkbox picker in `TaildropPanel`
- Remote API (FastAPI + uvicorn, optional)

See `CHANGELOG.md` for full version history.
