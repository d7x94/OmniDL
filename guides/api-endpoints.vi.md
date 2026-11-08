# Remote API Endpoints

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
