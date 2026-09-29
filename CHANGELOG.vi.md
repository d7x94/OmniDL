[English](CHANGELOG.md) | Tiếng Việt | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

Mọi thay đổi đáng chú ý của OmniDL được ghi lại trong file này.

Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Đánh số phiên bản theo [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Chưa có thay đổi nào chưa phát hành.

---

## v20.3.13 - 2026-09-29

### Đã sửa

- Cài đặt, Cookies theo nền tảng, CDP giờ báo lỗi rõ ràng khi không xác định được hồ sơ trình duyệt, thay vì mở hồ sơ `Default` và lưu cookies của tài khoản khác. Hãy chọn hồ sơ ở ô Hồ sơ cạnh ô trình duyệt trước. Nút CDP của Cookie chung, mục tài khoản TikTok và các nút yt-dlp giữ nguyên hành vi cũ (BUG-CDP-NO-PROFILE)
- Lấy cookies OK.ru theo nền tảng đã hoạt động. OK.ru có trong Cài đặt nhưng thiếu danh sách domain cookie, nên mọi lần lấy, cả CDP và yt-dlp, đều lọc sạch cookies và báo "không có cookies cho nền tảng này" (BUG-COOKIE-OKRU)

---

## v20.3.12 - 2026-09-29

### Đã sửa

- Lấy cookies trong Cài đặt, mục Mạng giờ đọc đúng hồ sơ trình duyệt bạn chọn ở ô Hồ sơ mới, cạnh ô chọn trình duyệt. Trước đây CDP theo nền tảng luôn mở hồ sơ `Default`, nên Instagram nhận cookies của tài khoản khác. Hồ sơ không tồn tại, tên hiển thị thay cho tên thư mục hồ sơ, hoặc cổng đã bị phiên trình duyệt khác chiếm giờ đều báo lỗi rõ ràng. Lần lấy CDP thất bại không còn giữ file cookie cũ và báo thành công (BUG-CDP-PROFILE)

---

## v20.3.11 - 2026-09-29

### Đã sửa

- Bài ảnh Facebook và Instagram giờ hiện đúng tên tác giả và tiêu đề trước khi tải, và lỗi đăng nhập của gallery-dl được báo là lỗi đăng nhập thay vì "không có nội dung". Bước dò metadata đọc một định dạng `--dump-json` mà gallery-dl không bao giờ ghi ra nên không tìm được gì (BUG-GDL-DUMP)
- Link ảnh X có đuôi `/photo/N` được tải bằng gallery-dl thay vì thất bại sau ba lần thử với "Media #N is not a video" (BUG-X-PHOTO-N)
- Tweet X cần đăng nhập (NSFW hoặc riêng tư) báo lỗi ngay kèm gợi ý cookie thay vì bị thử lại ba lần (BUG-X-AUTH)
- Link rút gọn `t.co` giờ dùng cookie và giới hạn tốc độ của X (BUG-X-TCO)
- Bấm Hủy giờ dừng ngay việc tải link CDN Instagram được dán vào (BUG-IG-CDN-CANCEL)
- Lỗi mạng khi đang tải Facebook Story không còn để lại video bị cụt mà chuyển sang cách bắt tiếp theo (BUG-FB-STORY-TRUNC)
- Luồng phân tích của giao diện web và route phân tích clipboard giờ từ chối link Facebook Story và waaw trên máy không có trình duyệt để điều khiển, giống `/api/analyse` (BUG-API-CDP-GUARD)
- Mục theo dõi trang Facebook giữ lại URL trang sau khi Hủy hoặc khi buổi live đã kết thúc, nên lần kiểm tra sau không còn lỗi "no username" (BUG-MON-URL)
- Log debug của gallery-dl không còn in mật khẩu proxy và đường dẫn file cookie tạm (BUG-GDL-LOG-ARGV)
- Lỗi gallery-dl chỉ chứa từ như "generate" không còn bị báo là bị giới hạn tốc độ (BUG-GDL-RATE-SUBSTR)
- Task TikTok bị hủy khi đang chờ slot tài khoản giờ kết thúc ở trạng thái đã hủy thay vì nằm chờ mãi (BUG-TT-POOL-CANCEL)
- Thông báo FFmpeg không có dữ liệu nêu đúng giới hạn 20 giây thay vì 120 (BUG-TT-STALL-TEXT)
- Video Facebook lỗi "Cannot parse data" một lần được thử lại thay vì chuyển sang gallery-dl, khi bước phân tích đã tìm thấy định dạng video (BUG-FB-PARSE-VIDEO)
- Giá trị `Retry-After` trong thông báo lỗi được giới hạn tối đa 120 giây, nên một giá trị lớn không còn giữ một worker hàng giờ (BUG-RETRY-AFTER-CAP)
- Tab Trang chủ giờ hiện lỗi khi không thể bắt đầu tải (ví dụ thư mục không ghi được) thay vì im lặng thất bại (BUG-HOME-START-ERROR)

---

## v20.3.10 - 2026-09-28

### Đã sửa

- Khi bài ảnh chuyển từ yt-dlp sang gallery-dl, bước dọn dẹp không còn xoá file mà các lượt tải khác đang ghi vào cùng thư mục; giờ chỉ xoá file mang mã media mà yt-dlp đã ghi cho chính task này (BUG-BU-SWEEP)
- Bản ghi của Theo dõi live bị huỷ từ hàng đợi nhưng giữ file dở (`PARTIAL_SAVED`) không còn làm dòng theo dõi kẹt mãi ở "Đang ghi"; dòng đó kết thúc và việc theo dõi tài khoản tiếp tục (BUG-MON-PARTIAL)
- Khi lấy danh sách video của trang cá nhân TikTok, Instagram hoặc X, cookie đã giải mã không còn bị xoá trước khi yt-dlp đọc, nên trang riêng tư được đọc khi đã đăng nhập và không còn file cookie dạng chữ thường bị ghi lại (BUG-PROFILE-COOKIE)
- Huỷ một lượt tải bằng gallery-dl (ảnh Instagram, Facebook, X) giờ dừng gallery-dl ngay thay vì chờ nó tải xong (BUG-GDL-CANCEL)
- Lượt tải gallery-dl bị huỷ hoặc lỗi không còn để lại file cookie phiên đã giải mã trên ổ đĩa (BUG-GDL-COOKIE-LEAK)
- Bài Instagram mà gallery-dl không tải được (cần đăng nhập, riêng tư, bị giới hạn) giờ báo lỗi thay vì báo hoàn tất mà không có file (BUG-GDL-IG-SILENT)
- Bài X chỉ có ảnh giờ được tải bằng gallery-dl thay vì thất bại sau 3 lần thử với lỗi "No video could be found in this tweet" (BUG-X-PHOTO)
- Gửi cùng một link rút gọn TikTok tới Remote API hai lần không còn tạo bản ghi thứ hai cho cùng một buổi live (BUG-DUP-CANONICAL)

---

## v20.3.9 - 2026-09-28

### Đã sửa

- Ghi livestream Instagram không còn ghi hai traceback asyncio `CancelledError` vào log mỗi khi đóng trình duyệt sau khi lấy được URL stream; các route chặn stream được gỡ trước khi Playwright tắt (BUG-IG-ROUTE-TEARDOWN)
- Khi FFmpeg lỗi với livestream Instagram, log giờ giữ nguyên vẹn tối đa 40 dòng lỗi cuối thay vì 600 ký tự cuối, vốn cắt mất đúng dòng nêu nguyên nhân (BUG-IG-STDERR-TAIL)

---

## v20.3.7 - 2026-09-20

### Đã sửa

- Bài đăng Facebook có nhiều ảnh không còn bị tải nhầm thành video quảng cáo không liên quan thay vì ảnh thật (BUG-FB-ADVID)

### Đã thêm

- Thông báo lỗi mới khi một bài đăng Facebook chỉ xác định được là quảng cáo và không thể tải xuống (`err.fb_post_advert_only`)

---

## v20.3.6 - 2026-09-20

### Đã sửa

- Bài đăng Facebook có nhiều ảnh không còn bị phân tích nhầm thành video quảng cáo (BUG-FB-ADVID)
- Bài đăng trong nhóm Facebook (`facebook.com/groups/.../posts/...`) giờ được nhận diện và có thể tải xuống

---

## v20.3.5 - 2026-09-20

### Đã sửa

- Bài đăng Facebook có nhiều ảnh không còn bị tải nhầm thành video quảng cáo của người khác (BUG-FB-ADVID)
- Yêu cầu phân tích Facebook thất bại giờ xuất hiện trong log thay vì không để lại dấu vết

---

## v20.3.4 - 2026-09-20

### Đã sửa

- TikTok: livestream đã kết thúc đôi khi vẫn bị báo là bị chặn/đang live tới 30 phút sau đó
- TikTok: một lỗi giới hạn tốc độ (HTTP 429) không còn khiến hệ thống bật lại phương thức phát hiện mà TikTok thực sự đã chặn

---

## v20.3.3 - 2026-09-19

### Đã sửa

- Dò bộ mã hóa phần cứng không còn tốn thời gian dò VideoToolbox (chỉ có trên macOS) trên Windows và Linux
- Thông báo lỗi rõ ràng hơn khi bộ mã hóa phần cứng dò thất bại

---

## v20.3.2 - 2026-09-17

### Đã sửa

- Giảm log lặp lại để dễ tìm lỗi và cảnh báo thật trong `omnidl.log`
- Sửa một cảnh báo yt-dlp bị lặp lại ở mỗi lần thử lại TikTok
- Thông báo lỗi Taildrop giờ hiển thị lỗi thật thay vì cảnh báo giả

---

## v20.3.1 - 2026-09-10

### Đã sửa

- TikTok: livestream đã kết thúc vẫn có thể bị báo là đang live ở lần kiểm tra sau
- TikTok: sự cố mất mạng không còn bị hiểu nhầm là lượt kiểm tra hoạt động bình thường
- Album ảnh Facebook gửi qua Taildrop không còn bị đặt tên "Unknown"

---

## v20.3.0 - 2026-09-09

### Đã thêm

- Nhận diện thêm nhiều dạng URL ảnh/album/bài đăng Facebook

### Đã sửa

- Bài đăng Facebook chứa ảnh giờ có thể tải xuống (trước đây luôn thất bại)
- Bài đăng Facebook mở qua link chia sẻ (`/share/p/...`) giờ được xử lý và tải xuống đúng
- Bài đăng Facebook vừa ảnh vừa video giờ lưu cả hai thay vì chỉ lưu ảnh

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
