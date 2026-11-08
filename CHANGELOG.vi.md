[English](CHANGELOG.md) | Tiếng Việt | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

Mọi thay đổi đáng chú ý của OmniDL được ghi lại trong file này.

Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Đánh số phiên bản theo [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Chưa có thay đổi nào chưa phát hành.

---

## v20.3.19 - 2026-10-06

### Đã sửa

- Settings: đổi theme trong Settings giờ cập nhật nhãn nút theme trên thanh trên cùng
- Settings: làm mới cookie tài khoản TikTok không còn kẹt ở trạng thái "đang làm mới" sau khi xong
- Settings: khởi động lại API thất bại sau khi reset Tailscale HTTPS sẽ tắt HTTPS lại và làm mới nhãn token, giống luồng bật
- Settings: Global CDP extract với trình duyệt không hỗ trợ báo lỗi trước hộp xác nhận thay vì sau
- Settings: giá trị thanh trượt (ví dụ số tải đồng thời) được lưu ngay mỗi lần đổi, không còn mất khi đóng app trong 500 ms
- Settings: công tắc Remote API, xoay token và điều khiển Tailscale HTTPS bị khóa khi một thao tác đang chạy, nên hai thao tác không chồng nhau
- Settings: đổi ngôn ngữ hoặc theme chờ các worker của Settings chạy xong rồi mới dựng lại trang, nên worker xong muộn không bật lại nút của lượt mới

---

## v20.3.18 - 2026-10-05

### Đã sửa

- Tab Home: đổi theme giờ vẽ lại màn hình chào, thẻ kết quả, thẻ chất lượng và huy hiệu thay vì để màu cũ khó đọc
- Tab Home: ảnh thu nhỏ của mục trước tải chậm không còn hiện trên kết quả không có ảnh thu nhỏ
- Tab Home: kết quả live xóa dòng trạng thái cũ (ghi chú ảnh hoặc lỗi)
- Tab Home: tooltip của nút Browse và Add to queue đổi theo ngôn ngữ giao diện
- Remote API: POST /api/analyse và /api/clipboard/analyse hủy worker phân tích khi client ngắt kết nối hoặc hết thời gian chờ

---

## v20.3.17 - 2026-10-03

### Đã sửa

- Special Download: lần tải thứ hai không còn làm nút Mở thư mục / Xem bị vô hiệu; kết quả cũ được xóa khi bắt đầu lần chạy mới
- Nút Xóa của tab Hàng đợi không còn lỗi khi thiếu thành phần API tùy chọn (fastapi)
- Nút Xóa tất cả của tab Lịch sử không còn xóa các tác vụ đang có job Remote Convert chạy
- Luồng analyse: client rớt kết nối giữa chừng không còn làm mất job đang chạy, nên kết nối lại không tạo job trùng
- Xóa hàng đợi qua Remote API chỉ báo cáo các tác vụ thật sự bị loại trừ
- Xóa file qua Remote API cũng xóa bản ghi lịch sử tương ứng, Lịch sử không còn hiện Xem / Gửi / Chuyển đổi cho file đã xóa
- Nhãn Hiện/Ẩn panel của tab Editor giữ đúng khi đổi ngôn ngữ lúc tab đang ẩn

---

## v20.3.16 - 2026-10-02

### Đã sửa

- Callback cũ của nút Phân tích (từ yêu cầu đã bị thay thế) không còn xóa mất trạng thái nút hủy/spinner của lần phân tích mới hơn
- Cài đặt > Xóa dữ liệu giờ cũng dừng Remote API server đang chạy, dừng Tailscale Serve, và dựng lại pool tài khoản TikTok thay vì để chúng trỏ vào cấu hình đã xóa
- Các công tắc Remote API (API server, Tailscale HTTPS, đổi token) khóa thao tác khi đang xử lý và tự khôi phục nếu khởi động lại thất bại, thay vì để công tắc kẹt ở trạng thái gây hiểu lầm
- Tab Editor: xuất file và xem trước không còn tranh chấp nhau; bắt đầu xuất khi đang xem trước sẽ bị từ chối thay vì làm hỏng cả hai
- Nút Xóa ở tab Queue không còn xóa nhầm task đang có job Remote Convert hoạt động
- Tab Home hiển thị lỗi phân tích qua toast (trước đây im lặng ở một số luồng), nút Duyệt/Thêm vào hàng đợi và huy hiệu LIVE đã được dịch và có tooltip
- Bước kiểm tra trước khi tải Facebook Story (kiểm tra trình duyệt có đang chạy) không còn chặn luồng giao diện tới 5 giây — giờ chạy trên luồng nền
- Làm mới cookie TikTok không còn cho phép bấm lần hai khởi động trích xuất trùng lặp cho cùng tài khoản khi lần trước chưa xong
- Chuyển đổi nhiều file sau khi tải (gallery-dl) giờ đếm số file còn đang chuyển đổi thay vì chuyển nút sang "xong" ngay khi file đầu tiên hoàn tất; bảng chọn encoder tùy chỉnh không còn chặn việc dựng giao diện khi dò GPU
- Xóa item đang phân tích ở tab Batch không còn làm bộ đếm phân tích chạy về -1 và kẹt spinner; nút Thử lại bị khóa khi đang chạy một đợt
- Thẻ file ở tab Convert giờ đổi màu theo giao diện thay vì giữ nguyên giao diện lúc tạo; kết quả dò hỗ trợ whisper đến giữa lúc đang xử lý không còn bật sớm nút Phụ đề; chữ bộ lọc hộp thoại chọn file đã được dịch
- Live Monitor không còn import trực tiếp infrastructure/ để kiểm tra cookie (BUG-LM-INFRA-IMPORT); một bản ghi quay lại trạng thái Chờ sẽ đặt lại URL (BUG-MON-URL); các nút thao tác có tooltip; Kiểm tra ngay trên dòng lỗi kiểm tra lại đúng các điều kiện như Thêm URL
- Tiêu đề bảng Thông báo, nút "Xóa hết" và tooltip nút đóng từng mục giờ đã được dịch và cập nhật khi đổi ngôn ngữ
- Giới hạn kích thước bộ nhớ đệm phân tích không còn xóa nhầm một job SSE đang chạy chỉ vì nó cũ nhất, việc này từng phá vỡ cơ chế gộp yêu cầu trùng cho cùng URL
- Lỗi khóa file Windows (AUDIT-02) trên 3 route còn sót (nhánh xóa file queue kiểu gallery-dl, files/delete, files/rename) giờ trả về 409 thay vì 500
- `GET /api/queue/{task_id}/file` giờ từ chối phục vụ trực tiếp HTML/SVG/XML, khớp với cơ chế chống XSS lưu trữ của `GET /api/files/serve`
- `GET /api/convert/{job_id}/file` đọc trạng thái job và tên file qua một lần chụp nhanh nguyên tử thay vì đọc rời rạc không khóa
- Truyền Taildrop gặp lỗi sớm (thiếu file, không có node đích hợp lệ, hoặc dịch vụ đang tắt) giờ báo lỗi cho từng node dự kiến thay vì để chỉ báo truyền phía client kẹt mãi không có tín hiệu kết thúc
- `DELETE /api/history/{task_id}` không còn chặn event loop khi ghi lại file lịch sử xuống đĩa
- Xóa thư mục thất bại một phần (`DELETE /api/files/delete`) giờ vẫn xóa bản ghi queue/history cho các file đã xóa được, thay vì để tất cả trỏ vào đường dẫn gần như đã mất

---

## v20.3.14 - 2026-09-30

### Đã sửa

- "Dùng cookies từ trình duyệt" giờ đọc đúng hồ sơ trình duyệt đã chọn ở Cài đặt > Mạng khi tải bằng yt-dlp và gallery-dl. Trước đây yt-dlp đọc hồ sơ dùng gần nhất, gallery-dl tự chọn hồ sơ, nên có thể tải bằng tài khoản khác (BUG-COOKIE-PROFILE-DL)
- Tải Facebook Story mở trình duyệt đúng hồ sơ đã chọn trong Cài đặt khi chọn cùng trình duyệt, và báo lỗi rõ nếu hồ sơ đó không còn (BUG-COOKIE-PROFILE-DL)
- File cookie xuất từ tiện ích trình duyệt giữ lại các dòng `#HttpOnly_`: kho tài khoản TikTok không còn từ chối nhầm là "chưa đăng nhập", và ghi live TikTok/Facebook bằng FFmpeg gửi lại cookie phiên (BUG-COOKIE-HTTPONLY)
- Tải Kuaishou chỉ gắn cookie phiên cho host của Kuaishou. Trước đây lệnh tải qua Remote API có thể gửi cookie tới bất kỳ host nào ghi trong URL (BUG-KS-COOKIE-HOST)
- Lần lấy cookies bị ngắt do lỗi không còn để lại vĩnh viễn toàn bộ cookies trình duyệt (`_tmp_*`) trong thư mục cookies; file được xoá ở lần mở app sau (BUG-COOKIE-TMP-JAR)
- Trong lúc đang lấy cookies, nút Chọn và Xoá của cùng ô cookie bị khoá, nên kết quả lấy xong không ghi đè file vừa chọn hoặc đăng ký lại ô vừa xoá (BUG-COOKIE-EXTRACT-RACE)

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

## v20.3.8 - 2026-09-23

### Đã sửa

- Remote App: khi điện thoại mất kết nối giữa lúc phân tích rồi nối lại, cùng một link không còn bị phân tích lại từ đầu mà chờ kết quả của lần đang chạy
- Taildrop: khi gửi thất bại vì lý do khác tên file (ví dụ `502 Bad Gateway`, không liên lạc được iPhone), không còn gửi lại cả file dưới tên ASCII

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

Older versions (v20.2.1 and earlier): [guides/CHANGELOG_ARCHIVE.vi.md](guides/CHANGELOG_ARCHIVE.vi.md).
