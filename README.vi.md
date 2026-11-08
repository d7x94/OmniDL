[English](README.md) | Tiếng Việt | [简体中文](README.zh-CN.md)

# OmniDL v20.3.29

Ứng dụng desktop tải xuống media từ YouTube, TikTok, Instagram, Twitter/X, Facebook và hơn 1000 trang web — xây dựng bằng Python, PySide6, yt-dlp và gallery-dl.

## Tính năng chính

- **Tải xuống** video, audio và ảnh từ hơn 1000 nền tảng qua yt-dlp và gallery-dl
- **Tải hàng loạt** — dán nhiều URL cùng lúc; có độ trễ riêng theo từng nền tảng để tránh bị giới hạn tốc độ
- **Tải đặc biệt** — Facebook Story và Instagram Live (qua CDP, giao diện debug trình duyệt)
- **Ảnh, album và bài đăng Facebook** — bài đăng ảnh, album và bài đăng ảnh + video, mỗi bài một thư mục. Video gợi ý và quảng cáo bị lọc bỏ
- **Theo dõi livestream** — tự ghi hình khi có livestream (Instagram và Facebook cần cookie, TikTok thì không)
- **Chuyển đổi** file đã tải sang MP4, MP3, MKV, AVI bằng FFmpeg. Bộ mã hóa phần cứng (NVENC/QSV/AMF/VideoToolbox) được dò tự động, luôn có phương án dự phòng chạy trên CPU
- **Trình chỉnh sửa video** — xem trước, cắt, xoay, tắt tiếng file đã tải
- **Nén/Giải nén** — nén file thành 7z/ZIP (có thể đặt mật khẩu), giải nén file nén sẵn có
- **Tài liệu** — chuyển đổi Markdown ↔ PDF, HTML ↔ PDF và Office ↔ PDF (tab desktop + Remote API); xem [Chuyển đổi tài liệu](guides/document-conversion.vi.md)
- **Nhóm tài khoản TikTok** — dùng nhiều cookie TikTok, mỗi cookie một profile trình duyệt, có kiểm tra trạng thái
- **Giao diện đa ngôn ngữ** — Tiếng Anh, Tiếng Việt và Tiếng Trung, trên ứng dụng desktop và web app
- **Quản lý cookie** theo từng nền tảng — nhập từ trình duyệt, yt-dlp hoặc CDP (Brave/Chrome 127+); mã hóa khi lưu
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
uv sync --group dev
```

Kèm hỗ trợ Remote API:

```bash
uv sync --extra api --group dev
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

Giữ bí mật token API — ai có token đều điều khiển được OmniDL và xem được lịch sử tải xuống. Không mở port 8765 ra internet công khai; dùng [Tailscale](guides/remote-iphone.vi.md) để truy cập an toàn từ ngoài mạng LAN.

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
uv sync --group dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

Ngưỡng coverage: `fail_under = 80` — không được hạ thấp.

CI chạy trên mỗi push/PR: `ruff check → mypy → pytest` (Python 3.11/3.12/3.13) cùng job bảo mật song song `bandit -ll → pip-audit`.

### Đóng gói bản build

Bản release được build tự động trong CI (`.github/workflows/build.yml`) khi push tag `v*.*.*`: PyInstaller build cho Windows và macOS, cộng thêm gói tarball Linux, tất cả đăng lên GitHub Release.

Build cục bộ (Python 3.13):

```bash
uv sync --extra build --group dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

Phân phối cả thư mục `dist/OmniDL/`, không chỉ file thực thi.

## Giấy phép

[MIT](LICENSE)

## Tài liệu thêm

- [Điều khiển từ xa qua iPhone (Remote API + Taildrop)](guides/remote-iphone.vi.md)
- [Chuyển đổi tài liệu](guides/document-conversion.vi.md)
- [Remote API endpoints](guides/api-endpoints.vi.md)
- [Dependencies](guides/dependencies.vi.md)

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

## Changelog

Lịch sử phiên bản đầy đủ, kèm ghi chú từng bug fix, nằm trong [`CHANGELOG.md`](CHANGELOG.md).
