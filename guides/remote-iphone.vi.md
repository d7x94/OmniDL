# Điều khiển từ xa qua iPhone (Remote API + Taildrop)

Điều khiển OmniDL từ iPhone, và gửi thẳng file đã tải xong đến điện thoại, qua Tailscale. Hai tính năng hoạt động độc lập nhưng phối hợp tốt với nhau.

**Yêu cầu**

- Đã cài Tailscale trên cả máy tính và iPhone — [tailscale.com](https://tailscale.com)
- Cả hai thiết bị đăng nhập cùng tài khoản Tailscale (cùng tailnet)
- Đã cài extra Remote API: `uv sync --extra api --group dev`

## 1. Kết nối hai thiết bị bằng Tailscale

Cài Tailscale trên cả hai thiết bị, đăng nhập cùng tài khoản, xác nhận máy tính xuất hiện trong ứng dụng Tailscale trên iPhone. Ghi lại tên MagicDNS của máy tính (tên host tự động của Tailscale cho một thiết bị, ví dụ `my-laptop`) — tên này không đổi dù IP thay đổi.

## 2. Bật Remote API trong OmniDL

Mở **Settings → Remote API** và bật lên. OmniDL tạo token API ngẫu nhiên và lưu trong keyring hệ thống (Windows Credential Manager / macOS Keychain, không bao giờ lưu trong file cấu hình). Server khởi động tại `http://0.0.0.0:8765`, lắng nghe trên mọi interface kể cả Tailscale.

Mọi request cần header `X-API-Token`. Không mở port 8765 ra internet công khai khi chưa qua Tailscale.

## 3. Mở ứng dụng trên iPhone

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

## 4. Thiết lập Taildrop để nhận file

Taildrop gửi file trực tiếp giữa các thiết bị Tailscale — không qua cloud.

Trong **Settings → Taildrop**, bật lên, bấm **Detect nodes** để tìm thiết bị Tailscale đang online, tích chọn iPhone trong danh sách (có thể chọn nhiều thiết bị). Chọn chế độ gửi:

- **Ask** (mặc định) — chỉ gửi file khi bạn bấm "Send" thủ công
- **Always** — mọi file tải xong đều tự động gửi

Trên iPhone, thông báo Taildrop hiện ra cho mỗi file đến; chạm vào, chọn **Save**, file sẽ vào ứng dụng Files. Tên file chứa emoji hoặc ký tự đặc biệt được làm sạch tự động trước khi gửi.

Bạn cũng có thể gửi thủ công từ tab Queue trên desktop (nút **Send**) hoặc từ hàng đợi Remote App.

## Luồng hoạt động điển hình

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

## Tự khởi động cùng Windows

Tạo shortcut trỏ đến `OmniDL.exe`, bấm `Win + R`, gõ `shell:startup`, thả shortcut vào thư mục đó. OmniDL sẽ khởi động cùng Windows, Remote API / Taildrop sẽ sẵn sàng nếu đã bật từ trước.
