[English](CHANGELOG_ARCHIVE.md) | Tiếng Việt | [简体中文](CHANGELOG_ARCHIVE.zh-CN.md)

# Changelog archive

Versions v20.2.1 and older. Newer versions: [CHANGELOG.vi.md](../CHANGELOG.vi.md).

---

## v20.2.1 - 2026-09-09

### Đã thay đổi

- Lượt tải thất bại lần đầu nhưng thành công khi thử lại không còn bị ghi log như lỗi

### Đã sửa

- Tải TikTok đôi khi bị kiểm tra lại và xóa ngay sau khi hoàn tất thành công (lỗi mất dữ liệu)
- Bài đăng ảnh Facebook mở qua link chia sẻ không còn thất bại vĩnh viễn
- Lượt tải gallery-dl thất bại không còn bị thử lại khi lỗi không thể khắc phục được

---

## v20.2.0 - 2026-09-09

### Đã sửa

- Facebook Story: video câm tiếng đôi khi bị báo nhầm là có âm thanh (Windows)
- Facebook Story giờ lưu vào thư mục đã chọn cho tác vụ thay vì luôn lưu vào thư mục mặc định
- Theo dõi Facebook Live không còn rò rỉ bộ nhớ khi theo dõi trang trong thời gian dài
- Tải Facebook Story không còn để hở kết nối mạng sau khi hết thời gian chờ
- Track âm thanh của Facebook Story không còn bị loại bỏ nhầm là thiếu
- Phiên bản app trả về qua `GET /api/ping` đã được sửa đúng

---

## v20.1.0 - 2026-09-09

### Đã thêm

- Tải bài đăng ảnh và album Facebook (ứng dụng desktop và Remote API)
- Album Facebook giờ lưu vào thư mục riêng
- Live Monitor có thể theo dõi trang và profile Facebook, tự ghi hình khi lên live

---

## v20.0.0 - 2026-09-04

### Đã thêm

- Chuyển đổi nhiều file cùng lúc, có thể cấu hình số lượng chạy song song
- Remote API: endpoint chuyển đổi file hàng loạt
- Thông báo lỗi từ engine và lỗi tải xuống giờ được dịch (Tiếng Anh / Tiếng Việt / Tiếng Trung)
- Nhóm tài khoản TikTok: mỗi profile trình duyệt một tài khoản, kiểm tra trạng thái trước khi chấp nhận, đổi tên/làm mới an toàn, tự dọn file cookie của tài khoản đã xóa
- Chuyển đổi tài liệu: Markdown, HTML và file Office sang PDF và ngược lại (tab Documents + Remote API)
- Tính năng Nén/Giải nén: nén thành ZIP/7z (có thể đặt mật khẩu), giải nén và xem trước file nén
- Bộ tải mới cho waaw.ac
- Tải ẩn danh một link CDN Instagram/Facebook đã ký sẵn được dán vào
- Phát hiện livestream TikTok: thêm phương thức phát hiện thứ 4 sau khi cả 3 phương thức cũ bị thay đổi chống bot vô hiệu hóa

### Đã sửa

- Facebook Story: thông báo lỗi rõ ràng hơn khi trình duyệt đang mở sẵn, thay vì lỗi timeout kết nối mơ hồ
- Facebook Story: âm thanh không còn bị rớt ngẫu nhiên khi tải chậm
- Facebook Story: phân tích link và tải xuống không còn hoạt động khác nhau trên server Linux
- Livestream Facebook chỉ phát qua DASH (không có HLS) giờ ghi hình đúng thay vì bị cắt ngắn
- Livestream TikTok đã kết thúc có thể bị phát hiện nhầm là đang live trong nhiều giờ sau đó
- Thời gian chờ giới hạn tốc độ của TikTok giờ dùng chung cho cả nhóm tài khoản thay vì riêng từng tài khoản, tránh lãng phí request
- File đầu ra đang bị khóa hoặc đang mở giờ báo rõ "file đang được sử dụng" thay vì lỗi server
- Sửa nhiều lỗi cookie/nhóm tài khoản: dùng cookie chung khi mọi tài khoản đang tạm dừng, file cookie bị rò rỉ, trạng thái tài khoản cũ sau khi dựng lại nhóm
- Tên file chứa dấu tiếng Việt, chữ Hán hoặc emoji giờ gửi nguyên vẹn đến thiết bị đời mới thay vì luôn bị chuyển hết về ASCII (BUG-TD-NAME)
- Thêm ghi hình Facebook Live (trước đây chỉ tải được đoạn clip ngắn thay vì livestream)
- Nhiều sửa lỗi nhỏ: trạng thái tạm dừng/tiếp tục, đồng bộ hàng đợi/lịch sử, tab Files, Live Monitor, tab Settings
- Tab Nén/Giải nén: nút hiện/ẩn mật khẩu bị vô hình; tùy chọn "giữ tên gốc" giờ hoạt động đúng
- Facebook Story: hiện tượng chớp cửa sổ dòng lệnh trên Windows đã được xử lý triệt để
- Tên file đầu ra khi ghi hình livestream đã được sửa đúng trên mọi nền tảng
- Sửa lỗi treo ứng dụng khi khởi động ở bản build không có console, và lỗi công tắc "Verbose logging" trong Settings

### Bảo mật

- Chuyển đổi tài liệu: chặn truy cập file cục bộ và mạng từ nội dung HTML/Markdown không đáng tin (giới hạn đường dẫn, giới hạn dung lượng, giới hạn số lượng xử lý đồng thời)
- Endpoint xem trước file không còn render trực tiếp file HTML/SVG, ngăn chặn lỗ hổng cross-site scripting lưu trữ

---

## v19.0.0 - 2026-05-31

### Đã thay đổi

- Giao diện desktop viết lại bằng PySide6 (Qt6) với giao diện màu sắc mới
- Phát hiện livestream TikTok viết lại để chạy nhiều chiến lược phát hiện song song
- Giao diện Remote App (PWA) đã dịch hoàn toàn sang Tiếng Việt
- Cookie đã giải mã giờ được lưu tạm trong bộ nhớ, tránh gọi keychain hệ thống nhiều lần

### Đã thêm

- Nhóm tài khoản TikTok: dùng nhiều tài khoản, tự động cân bằng tải
- Panel cài đặt mạng: cấu hình proxy cho tải xuống

### Đã sửa

- ID phòng TikTok giờ được chuyển qua Remote API để các chiến lược dự phòng hoạt động đúng
- Giảm lỗi giới hạn tốc độ TikTok Live bằng cách giãn cách request

---

## v18.0.0 - 2026-04-30

### Đã thêm

- Ghi hình Instagram Live (chỉ Windows/macOS)
- Truy cập Remote API qua HTTPS bằng Tailscale
- Kiểm tra livestream profile TikTok (không cần cookie)

### Đã sửa

- TikTok Live báo sai là không live trong nhiều trường hợp (BUG-TT-08/09/10)
- Instagram Live chuyển sang streaming DASH sau khi Instagram đổi định dạng (BUG-IG-01)
- Kuaishou: sửa lỗi trích xuất lại sai do lỗi mạng tạm thời và lỗi thiếu cookie (BUG-KS-01/02)
- Sửa lỗi treo bộ mã hóa GPU có thể lặp lại sau lần chuyển đổi thất bại
- Sửa hiện tượng chớp cửa sổ dòng lệnh trên Windows khi tải Facebook Story

---

## v16.3.1 - 2026-03-22

### Đã sửa

- Sửa phiên bản app hiển thị sai trên sidebar và file cấu hình mẫu
- Kiểm tra dependency lúc khởi động giờ bao gồm cả Playwright

---

## v16.3.0 - 2026-03-21

### Đã gỡ bỏ

- Tab Trình chỉnh sửa video (gỡ bỏ để ứng dụng tập trung vào tải xuống)
- Bộ tải Threads (gỡ bỏ do chi phí bảo trì cao vì API thay đổi liên tục)

### Đã sửa

- Dọn dẹp code còn sót lại sau khi gỡ bỏ các tính năng trên

---

## v16.2.1 - 2026-03-21

### Đã sửa

- Bộ tải Threads: sửa sai endpoint API và app ID, cùng token xác thực bị hết hạn

---

## v16.2.0 - 2026-03-21

### Đã thêm

- Bộ tải Threads (video, ảnh, bài đăng dạng carousel)
- Hỗ trợ Facebook Story trên macOS
- Chọn độ phân giải trong Trình chỉnh sửa video (480p đến 4K)

### Đã sửa

- Nhiều lỗi Trình chỉnh sửa video: treo ứng dụng, đơ giao diện khi dò bộ mã hóa, hiệu ứng làm mờ sai, lỗi cài đặt CRF

---

## v16.1.0 - 2026-03-20

### Đã thêm

- Tab Tải đặc biệt, bắt đầu với Facebook Story

### Đã thay đổi

- Viết lại engine Facebook Story để dùng chính phiên đăng nhập của trình duyệt thay vì code kết nối tự viết

### Đã sửa

- Lượt tải trùng lặp không còn ghi đè file đã có
- Lượt tải dài giờ có giới hạn thời gian an toàn 5 phút thay vì bị treo
- Nút thao tác sau khi tải không còn bị ẩn sau khi tải thành công

---

## v16.0.0 - 2026-03-07

### Bảo mật

- Sửa lỗ hổng path traversal trong xử lý file cookie
- Loại bỏ rủi ro shell injection trong chức năng "mở trong file explorer"
- Dữ liệu người dùng (config, lịch sử, log) chuyển ra khỏi thư mục cài đặt sang vị trí ghi được riêng cho từng người dùng
- Sửa rủi ro server-side request forgery (SSRF) qua URL ảnh thu nhỏ
- Trích xuất cookie giờ kiểm tra tên trình duyệt theo danh sách cho phép
- Tham số yt-dlp tùy chỉnh giờ được lọc qua danh sách cho phép nghiêm ngặt
- Cập nhật dependency, bao gồm bản vá bảo mật cho Pillow

### Đã thay đổi

- Ghi config và lịch sử giờ atomic, giảm rủi ro hỏng dữ liệu
- Lưu trữ lịch sử chuyển sang định dạng append-only để tăng độ tin cậy
- Hủy livestream giờ phản hồi trong khoảng 10 giây thay vì tới 90 giây
