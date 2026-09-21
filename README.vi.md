[English](README.md) | Tiếng Việt | [简体中文](README.zh-CN.md)

# OmniDL v20.3.7

Ứng dụng desktop tải xuống media từ YouTube, TikTok, Instagram, Twitter/X, Facebook và hơn 1000 trang web — xây dựng bằng Python, PySide6, yt-dlp và gallery-dl.

## Tính năng chính

- **Tải xuống** video, audio và ảnh từ hơn 1000 nền tảng qua yt-dlp và gallery-dl
- **Tải hàng loạt** — dán nhiều URL cùng lúc; có độ trễ riêng theo từng nền tảng để tránh bị giới hạn tốc độ
- **Tải đặc biệt** — Facebook Story, Instagram Live qua CDP (Chrome DevTools Protocol, giao diện debug trình duyệt mà Playwright điều khiển)
- **Ảnh, album và bài đăng Facebook** — bài đăng ảnh, album và bài đăng vừa ảnh vừa video được tải qua gallery-dl và yt-dlp phối hợp, mỗi bài đăng lưu vào thư mục riêng; video gợi ý/quảng cáo Facebook chèn vào trang bài đăng bị lọc bỏ, để bài đăng ảnh không bao giờ bị tải nhầm thành quảng cáo của người khác
- **Theo dõi livestream** — tự động ghi hình khi có livestream; theo dõi profile Instagram (cần cookie), profile TikTok (không cần cookie), hoặc trang/profile Facebook (cần cookie)
- **Chuyển đổi** file đã tải sang MP4, MP3, MKV, AVI bằng FFmpeg. Bộ mã hóa phần cứng (NVENC/QSV/AMF/VideoToolbox) được dò tự động, luôn có phương án dự phòng chạy trên CPU
- **Trình chỉnh sửa video** — xem trước, cắt, xoay, tắt tiếng file đã tải
- **Nén/Giải nén** — nén file thành 7z/ZIP (có thể đặt mật khẩu), giải nén file nén sẵn có
- **Tài liệu** — chuyển đổi Markdown ↔ PDF, HTML ↔ PDF và Office ↔ PDF (tab desktop + Remote API); xem [Chuyển đổi tài liệu](#chuyển-đổi-tài-liệu)
- **Nhóm tài khoản TikTok** — thêm nhiều cookie TikTok, mỗi cookie ứng với một profile trình duyệt; mỗi cookie được kiểm tra trạng thái trước khi chấp nhận, có thể đổi tên hoặc làm mới tại chỗ
- **Giao diện đa ngôn ngữ** — Tiếng Anh, Tiếng Việt và Tiếng Trung, trên cả ứng dụng desktop và PWA (Progressive Web App — Remote UI cài đặt như ứng dụng gốc); lỗi từ engine và lỗi tải xuống cũng được dịch
- **Quản lý cookie** theo từng nền tảng — nhập từ trình duyệt, trích xuất qua yt-dlp, hoặc trích xuất qua CDP (Brave/Chrome 127+); mã hóa khi lưu trữ; tự dọn cookie mồ côi khi đổi phương thức
- **Truyền file Taildrop** — gửi file đến iPhone hoặc bất kỳ node Tailscale nào, hỗ trợ nhiều thiết bị
- **Hành động sau khi tải** — chuyển đổi, gửi qua Taildrop, hoặc xóa trực tiếp từ hàng đợi
- **Remote API** (tùy chọn) — điều khiển OmniDL từ iPhone hoặc bất kỳ trình duyệt nào trong mạng qua PWA tích hợp sẵn

## Yêu cầu hệ thống

- Python 3.11 trở lên
- Windows, macOS hoặc Linux
- FFmpeg đã đóng gói sẵn trong bản release; nếu chạy từ mã nguồn cần cài FFmpeg vào `PATH` để dùng Chuyển đổi/Trình chỉnh sửa/Tài liệu
- [Tailscale](https://tailscale.com) — chỉ cần khi dùng Remote API từ ngoài mạng máy tính của bạn

## Cài đặt

```bash
uv sync --extra dev
```

Kèm hỗ trợ Remote API:

```bash
uv sync --extra api --extra dev
```

## Chạy nhanh

### Desktop

```bash
uv run python main.py
```

### Remote UI / Web API

1. Mở **Settings → Remote API** trong ứng dụng desktop và bật lên. OmniDL tạo token API và lưu trong keyring hệ thống (không bao giờ lưu trong file cấu hình).
2. Server khởi động tại `http://0.0.0.0:8765`. Mọi request cần header `X-API-Token`.
3. Mở `http://<tên-máy-hoặc-IP>:8765` trên trình duyệt cùng mạng và nhập token.

Giữ bí mật token API — ai có token đều điều khiển được OmniDL và xem được lịch sử tải xuống. Không mở port 8765 ra internet công khai; dùng [Tailscale](#điều-khiển-từ-xa-qua-iphone-remote-api--taildrop) để truy cập an toàn từ ngoài mạng LAN.

## Cấu hình cơ bản

Cấu hình được chỉnh từ tab **Settings** trên desktop (thư mục tải xuống, số lần tải đồng thời tối đa, số lần thử lại, giao diện màu sắc, ngôn ngữ, file cookie, Remote API, Taildrop). Cấu hình lưu trong `config.json` bên trong [Thư mục dữ liệu](#thư-mục-dữ-liệu) — file này do ứng dụng tự ghi, không nên chỉnh tay.

Cấu hình chính:

| Cấu hình | Vị trí | Công dụng |
|---|---|---|
| Thư mục tải xuống | Settings → File Location | Nơi lưu file đã tải xong |
| Số lần tải đồng thời tối đa | Settings → Download Behaviour | Số lượng tải chạy song song |
| Ngôn ngữ giao diện | Settings → Appearance | `en` / `vi` / `zh` |
| File cookie | Settings → Network | Cookie theo từng nền tảng cho nội dung cần đăng nhập |
| Remote API | Settings → Remote API | Bật/tắt server điều khiển từ xa và token |

## Lỗi thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Safari/trình duyệt không mở được `http://my-laptop:8765` | Tailscale tắt, hoặc hai thiết bị khác tài khoản | Kiểm tra ứng dụng Tailscale, xác nhận cùng tailnet |
| "Unauthorized" trong Remote App | Token sai hoặc thiếu | Settings → Remote API → copy token, nhập lại |
| iPhone không xuất hiện trong Detect nodes | Tailscale trên iPhone tắt hoặc offline | Mở Tailscale trên iPhone, xác nhận đã kết nối |
| Taildrop báo lỗi `400 Bad Request` | Tên file chứa ký tự đặc biệt | OmniDL gửi tên Unicode đầy đủ trước, thử lại một lần với tên chuyển thành ASCII; nếu vẫn lỗi, cập nhật Tailscale trên iPhone |
| Gửi file xong nhưng không có thông báo | iOS tắt thông báo cho Tailscale | iPhone Settings → Notifications → Tailscale → cho phép |
| Remote App chậm / SSE (Server-Sent Events, dùng để cập nhật tiến trình trực tiếp) bị ngắt | Kết nối Tailscale không ổn định | Chuyển sang chế độ relay trong Tailscale settings |

## Cấu trúc thư mục

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, account_pool, config, history.
ui/              PySide6 tabs and widgets. Consumes app/service layer only.
utils/           Pure helpers — ffmpeg_locator, logger, live checkers, tiktok_detection/.
tests/           pytest unit tests. Mock-only — no real network or subprocess.
```

Chiều phụ thuộc: `ui -> app -> domain`, `infrastructure -> domain`, `utils` dùng được ở mọi nơi.

## Phát triển và kiểm thử

```bash
uv sync --extra dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

Ngưỡng coverage: `fail_under = 80` — không được hạ thấp.

CI chạy trên mỗi push/PR: `ruff check → mypy → pytest` (Python 3.11/3.12/3.13) cùng job bảo mật song song `bandit -ll → pip-audit`.

### Đóng gói bản build

Bản release được build tự động trong CI (`.github/workflows/build.yml`) khi push tag `v*.*.*`: PyInstaller build cho Windows và macOS, cộng thêm gói tarball Linux, tất cả đăng lên GitHub Release.

Build cục bộ (Python 3.13):

```bash
uv sync --extra build --extra dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

Phân phối cả thư mục `dist/OmniDL/`, không chỉ file thực thi.

## Giấy phép

[MIT](LICENSE)

---

## Điều khiển từ xa qua iPhone (Remote API + Taildrop)

Điều khiển OmniDL từ iPhone, và gửi thẳng file đã tải xong đến điện thoại, qua Tailscale. Hai tính năng hoạt động độc lập nhưng phối hợp tốt với nhau.

**Yêu cầu**

- Đã cài Tailscale trên cả máy tính và iPhone — [tailscale.com](https://tailscale.com)
- Cả hai thiết bị đăng nhập cùng tài khoản Tailscale (cùng tailnet)
- Đã cài extra Remote API: `uv sync --extra api --extra dev`

### 1. Kết nối hai thiết bị bằng Tailscale

Cài Tailscale trên cả hai thiết bị, đăng nhập cùng tài khoản, xác nhận máy tính xuất hiện trong ứng dụng Tailscale trên iPhone. Ghi lại tên MagicDNS của máy tính (tên host tự động của Tailscale cho một thiết bị, ví dụ `my-laptop`) — tên này không đổi dù IP thay đổi.

### 2. Bật Remote API trong OmniDL

Mở **Settings → Remote API** và bật lên. OmniDL tạo token API ngẫu nhiên và lưu trong keyring hệ thống (Windows Credential Manager / macOS Keychain, không bao giờ lưu trong file cấu hình). Server khởi động tại `http://0.0.0.0:8765`, lắng nghe trên mọi interface kể cả Tailscale.

Mọi request cần header `X-API-Token`. Không mở port 8765 ra internet công khai khi chưa qua Tailscale.

### 3. Mở ứng dụng trên iPhone

Trong Safari, vào `http://<tên-máy>:8765` (ví dụ `http://my-laptop:8765`) và nhập token API khi được hỏi. Dùng Share → "Add to Home Screen" để cài như ứng dụng gốc.

Nếu không kết nối được: xác nhận Tailscale đang bật trên cả hai thiết bị, hoặc thử IP Tailscale thay vì tên (ví dụ `http://100.64.0.5:8765`).

| Tính năng | Chức năng |
|---|---|
| Dán URL + Analyse | Phân tích link, chọn định dạng/chất lượng |
| Download | Bắt đầu tải, xem tiến trình trực tiếp |
| Queue | Xem toàn bộ tác vụ, tạm dừng/tiếp tục/hủy |
| Convert | Chuyển đổi file đã tải sang MP4 phù hợp iPhone |
| Preview | Xem video/ảnh ngay trên trình duyệt |
| Send (Taildrop) | Gửi file đến iPhone qua Tailscale |
| Delete | Xóa file khỏi máy tính |

### 4. Thiết lập Taildrop để nhận file

Taildrop gửi file trực tiếp giữa các thiết bị Tailscale — không qua cloud.

Trong **Settings → Taildrop**, bật lên, bấm **Detect nodes** để tìm thiết bị Tailscale đang online, tích chọn iPhone trong danh sách (có thể chọn nhiều thiết bị). Chọn chế độ gửi:

- **Ask** (mặc định) — chỉ gửi file khi bạn bấm "Send" thủ công
- **Always** — mọi file tải xong đều tự động gửi

Trên iPhone, thông báo Taildrop hiện ra cho mỗi file đến; chạm vào, chọn **Save**, file sẽ vào ứng dụng Files. Tên file chứa emoji hoặc ký tự đặc biệt được làm sạch tự động trước khi gửi.

Bạn cũng có thể gửi thủ công từ tab Queue trên desktop (nút **Send**) hoặc từ hàng đợi Remote App.

### Luồng hoạt động điển hình

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

### Tự khởi động cùng Windows

Tạo shortcut trỏ đến `OmniDL.exe`, bấm `Win + R`, gõ `shell:startup`, thả shortcut vào thư mục đó. OmniDL sẽ khởi động cùng Windows, Remote API / Taildrop sẽ sẵn sàng nếu đã bật từ trước.

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

**Tùy chọn — Remote API** (chỉ cần khi `api_enabled=True`, qua `uv sync --extra api`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for the remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as a daemon thread alongside the Qt event loop |

Dependency cho dev/build nằm trong nhóm `dev` và `build` của `pyproject.toml` (`uv sync --extra dev`).

## Chuyển đổi tài liệu

Tab **Documents** (và `POST /api/docs/convert`) chuyển đổi giữa các định dạng tài liệu. Hỗ trợ 7 hướng chuyển đổi:

| Từ | Sang | Back-end |
|---|---|---|
| Markdown (`.md`, `.markdown`, …) | PDF | `markdown` + WeasyPrint |
| Markdown | HTML | `markdown` |
| HTML (`.html`, `.htm`, `.xhtml`) | PDF | WeasyPrint |
| PDF | Markdown | `pypdf` |
| PDF | HTML | `pypdf` |
| PDF | DOCX | LibreOffice |
| Office (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.rtf`, `.csv`, …) | PDF | LibreOffice |

Hai back-end sau là **tùy chọn**, OmniDL vẫn hoạt động bình thường (giảm tính năng) khi thiếu — `GET /api/docs/capabilities` báo máy chủ hỗ trợ gì, tab desktop làm mờ phần không dùng được, API trả `503` kèm lý do rõ ràng:

- **LibreOffice** — bắt buộc cho mọi hướng Office. Không đóng gói sẵn (bộ cài ~700 MB); cài từ [libreoffice.org](https://www.libreoffice.org/), nút **Re-check** trên tab sẽ nhận diện ngay không cần khởi động lại OmniDL.
- **Pango / GTK** — WeasyPrint nạp Pango, HarfBuzz và fontconfig lúc import. Linux và macOS có sẵn qua trình quản lý gói; trên **Windows** cần cài [GTK for Windows Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer) để dùng Markdown/HTML → PDF.

**Không hỗ trợ PDF quét ảnh.** PDF → Markdown/HTML chỉ trích xuất lớp text; PDF không có text chọn được sẽ báo lỗi rõ ràng thay vì tạo ra file rỗng. Không có OCR.

**Bảo mật.** Một tài liệu có thể tham chiếu tài nguyên từ xa hoặc tùy ý qua `<img>`, `<link>`, `@import` hoặc CSS `url()`. Trình render chỉ cho phép URI `data:` và file nằm trong đúng thư mục chứa tài liệu gốc; mọi thứ khác bị từ chối, ghi log và bỏ qua (trang vẫn render bình thường, thiếu phần đó). Qua Remote API, cả `source_path` và `out_dir` đều bị giới hạn trong `download_dir`, nguồn trên 200 MiB bị từ chối, số lượng xử lý đồng thời giới hạn tối đa 2.

## Thư mục dữ liệu

| OS | Đường dẫn |
|---|---|
| Windows | `%APPDATA%\OmniDL\` |
| macOS | `~/Library/Application Support/OmniDL/` |
| Linux | `~/.local/share/OmniDL/` |

File cookie nằm trong `<data_dir>/cookies/` và được mã hóa khi lưu trữ (DPAPI, cơ chế mã hóa theo từng người dùng có sẵn của Windows, trên Windows; Fernet trên macOS).
Log ghi vào thư mục log của hệ điều hành (`%LOCALAPPDATA%\OmniDL\Logs\` trên Windows,
`~/Library/Logs/OmniDL/` trên macOS, `~/.local/state/OmniDL/log/` trên Linux):
`omnidl.log` ở mức INFO luôn ghi, cộng thêm `omnidl_debug.log` ở mức DEBUG khi bật *Verbose logging* trong Settings. Cả hai xoay vòng ở 5 MB, giữ tối đa 3 file.

## Remote API Endpoints

Khi `api_enabled=True`, server chạy tại `http://0.0.0.0:8765`. Mọi request cần header `X-API-Token`.

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/ping` | Kiểm tra tình trạng — trả về phiên bản app và cờ khả năng `facebook_story` (Story cần Brave/Chrome cài trên máy, nên chỉ `true` trên Windows và macOS) |
| POST | `/api/analyse` | Phân tích URL, trả về MediaInfo |
| GET | `/api/analyse/stream` | Phân tích URL qua SSE (tiến trình trực tiếp) |
| POST | `/api/clipboard/analyse` | Phân tích URL đang có trong clipboard |
| POST | `/api/download` | Bắt đầu tải xuống |
| POST | `/api/download/batch` | Thêm tối đa 500 lượt tải trong một lần gọi (tab Batch) |
| GET | `/api/queue` | Liệt kê toàn bộ tác vụ |
| GET | `/api/queue/{task_id}` | Lấy trạng thái một tác vụ |
| POST | `/api/queue/{task_id}/pause` | Tạm dừng tác vụ |
| POST | `/api/queue/{task_id}/resume` | Tiếp tục tác vụ |
| POST | `/api/queue/{task_id}/cancel` | Hủy tác vụ |
| DELETE | `/api/queue/items` | Xóa tác vụ khỏi hàng đợi theo id |
| DELETE | `/api/queue/finished` | Xóa các tác vụ đã hoàn tất/thất bại khỏi hàng đợi |
| POST | `/api/queue/{task_id}/transfer` | Gửi file qua Taildrop |
| POST | `/api/queue/{task_id}/rename` | Đổi tên file đầu ra của tác vụ đã hoàn tất |
| DELETE | `/api/queue/{task_id}/file` | Xóa file khỏi ổ đĩa |
| GET | `/api/queue/{task_id}/file` | Stream/xem trước file (hỗ trợ Range request) |
| GET | `/api/queue/{task_id}/fileinfo` | Thông tin file (tên, dung lượng, còn tồn tại hay không) |
| GET | `/api/convert/encoders` | Liệt kê bộ mã hóa GPU/CPU khả dụng |
| GET | `/api/convert/codecs` | Liệt kê codec đầu ra + hỗ trợ phụ đề của bản FFmpeg hiện tại |
| GET | `/api/convert/concurrency` | Giới hạn chuyển đổi song song hiện tại và mức tối đa server cho phép |
| POST | `/api/convert/concurrency` | Đặt số lượt chuyển đổi chạy song song (1-8, được lưu lại) |
| POST | `/api/queue/{task_id}/convert` | Bắt đầu tác vụ chuyển đổi |
| POST | `/api/queue/{task_id}/subtitles` | Tạo phụ đề (.srt), không mã hóa lại video |
| GET | `/api/convert/{job_id}` | Lấy trạng thái một tác vụ chuyển đổi |
| POST | `/api/convert/{job_id}/cancel` | Hủy tác vụ chuyển đổi |
| GET | `/api/convert/{job_id}/file` | Stream file đã chuyển đổi (iOS Safari); `?kind=srt` để lấy file phụ đề |
| DELETE | `/api/convert/{job_id}/file` | Xóa file đã chuyển đổi |
| GET | `/api/history/stats` | Thống kê lịch sử tải xuống |
| GET | `/api/history` | Lịch sử tải xuống |
| DELETE | `/api/history/{task_id}` | Xóa một mục lịch sử |
| GET | `/api/files/browse` | Duyệt file/thư mục trên máy tính |
| POST | `/api/files/convert` | Chuyển đổi một file bất kỳ (ngoài hàng đợi) |
| POST | `/api/files/convert/batch` | Xếp hàng tối đa 100 file với cùng một bộ cài đặt |
| POST | `/api/files/subtitles` | Tạo phụ đề (.srt) cho một file bất kỳ |
| DELETE | `/api/files/delete` | Xóa file theo đường dẫn |
| GET | `/api/files/serve` | Stream file theo đường dẫn tuyệt đối |
| POST | `/api/files/transfer` | Gửi file theo đường dẫn qua Taildrop |
| GET | `/api/nodes` | Liệt kê các node Taildrop đã cấu hình |
| POST | `/api/archive/compress` | Nén file thành 7z/ZIP (có thể đặt mật khẩu) |
| POST | `/api/archive/extract` | Giải nén một file nén |
| POST | `/api/archive/contents` | Xem trước nội dung file nén trước khi giải nén |
| GET | `/api/docs/capabilities` | Báo back-end chuyển đổi tài liệu nào máy chủ có |
| POST | `/api/docs/convert` | Chuyển đổi tài liệu (Markdown/HTML/Office ↔ PDF) |
| GET | `/api/monitor` | Liệt kê các livestream đang theo dõi |
| POST | `/api/monitor` | Thêm profile/URL vào danh sách theo dõi livestream (TikTok, Instagram, trang/profile Facebook, hoặc URL livestream trực tiếp) |
| POST | `/api/monitor/interval` | Đặt chu kỳ kiểm tra livestream |
| DELETE | `/api/monitor/{item_id}` | Ngừng theo dõi |
| POST | `/api/monitor/{item_id}/cancel` | Ngừng ghi hình, vẫn tiếp tục theo dõi |
| POST | `/api/monitor/{item_id}/pause` | Tạm dừng kiểm tra livestream cho một link |
| POST | `/api/monitor/{item_id}/resume` | Tiếp tục kiểm tra livestream cho một link |
| POST | `/api/monitor/{item_id}/check-now` | Kiểm tra trạng thái livestream ngay lập tức |
| POST | `/api/monitor/pause` | Tạm dừng toàn bộ live monitor |
| POST | `/api/monitor/resume` | Tiếp tục toàn bộ live monitor |
| GET | `/api/settings/language` | Ngôn ngữ giao diện hiện tại + danh sách ngôn ngữ hỗ trợ |
| POST | `/api/settings/language` | Đặt ngôn ngữ giao diện (`en` / `vi` / `zh`) |
| GET | `/api/events` | Luồng SSE — tiến trình/trạng thái thời gian thực |
| GET | `/` | PWA (ứng dụng web cho iPhone) |

## Changelog

Lịch sử phiên bản đầy đủ, kèm ghi chú từng bug fix, nằm trong [`CHANGELOG.md`](CHANGELOG.md).
