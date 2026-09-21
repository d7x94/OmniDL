"""Translation catalogue for the OmniDL desktop UI.

One dict per language, flat ``dotted.key -> text``.  Vietnamese is the source
language (the original hard-coded strings); English and Chinese are translations.

Keep every language's key set identical — ``tests/test_i18n.py`` enforces it.
"""

from __future__ import annotations

VI: dict[str, str] = {
    # ── Navigation ────────────────────────────────────────────────────────
    "nav.home": "Tải xuống",
    "nav.queue": "Hàng đợi",
    "nav.batch": "Hàng loạt",
    "nav.live_monitor": "Trực tiếp",
    "nav.convert": "Chuyển đổi",
    "nav.editor": "Chỉnh sửa",
    "nav.archive": "Nén/Giải nén",
    "nav.history": "Lịch sử",
    "nav.settings": "Cài đặt",
    "nav.special_dl": "Đặc biệt",
    "nav.section.download": "TẢI XUỐNG",
    "nav.section.tools": "CÔNG CỤ",
    "nav.section.library": "THƯ VIỆN",
    "nav.section.system": "HỆ THỐNG",
    # ── Top bar ───────────────────────────────────────────────────────────
    "topbar.theme_light": "☀ Sáng",
    "topbar.theme_dark": "🌙 Tối",
    "topbar.notifications": "Thông báo",
    "topbar.hide_nav": "Ẩn thanh điều hướng",
    "topbar.nav_strip": "Thanh điều hướng",
    "topbar.toolbar_strip": "Thanh phân tích",
    "topbar.theme_changed": "Đổi giao diện — khởi động lại app để áp dụng cho mọi tab.",
    # ── Toolbar ───────────────────────────────────────────────────────────
    "toolbar.url_placeholder": "Dán link video vào đây và nhấn Enter hoặc nhấp Phân tích...",
    "toolbar.analyse": "Phân tích",
    "toolbar.analysing": "Đang phân tích",
    "toolbar.analysing_dots": "Đang phân tích...",
    "toolbar.stop": "Dừng",
    "toolbar.paste_tip": "Dán từ clipboard",
    "toolbar.clear_tip": "Xóa URL",
    "toolbar.collapse_tip": "Ẩn thanh phân tích",
    "toolbar.status.clipboard": "URL từ clipboard - nhấn Enter để phân tích",
    "toolbar.status.loading": "Đang tải thông tin media...",
    "toolbar.status.cancelled": "Đã hủy.",
    "toolbar.status.no_media": "Không tìm thấy media",
    "toolbar.status.pasted": "Đã dán URL",
    "toolbar.toast.no_result": "Phân tích không trả về kết quả.",
    "toolbar.toast.ready": "Sẵn sàng: {title}",
    "toolbar.toast.failed": "Phân tích thất bại.",
    "toolbar.error.unknown": "Lỗi không xác định",
    # ── Queue tab ─────────────────────────────────────────────────────────
    "queue.title": "Hàng đợi tải xuống",
    "queue.select": "Chọn",
    "queue.select_tip": "Chọn nhiều tác vụ",
    "queue.clear_finished": "Xóa đã xong",
    "queue.clear_selected": "Xóa đã chọn ({count})",
    "queue.empty": "Không có tác vụ nào\nDán URL vào tab Tải xuống để bắt đầu",
    "queue.count": "  {active} đang tải  ·  {total} tổng  ",
    "queue.rename_title": "Đổi tên file",
    "queue.rename_label": "Tên file mới:",
    "queue.no_target": "Chưa cấu hình thiết bị đích trong Settings → Taildrop",
    "queue.taildrop_off": "Taildrop chưa được bật trong Settings",
    "queue.sent_to": "📲  Đã gửi → {node}",
    "queue.send_failed": "❌  Gửi thất bại → {node}: {err}",
    "queue.sending": "📲  Đang gửi đến {count} thiết bị: {nodes}",
    "queue.send_error": "❌  Lỗi gửi file: {err}",
    # ── Settings ──────────────────────────────────────────────────────────
    "settings.search": "Tìm kiếm cài đặt...",
    "settings.no_results": "Không tìm thấy cài đặt nào khớp.",
    "settings.section.location": "VỊ TRÍ LƯU FILE",
    "settings.section.behaviour": "HÀNH VI TẢI XUỐNG",
    "settings.section.appearance": "GIAO DIỆN",
    "settings.section.clipboard": "THEO DÕI CLIPBOARD",
    "settings.section.developer": "NHÂN VIÊN PHÁT TRIỂN",
    "settings.browse": "Duyệt...",
    "settings.max_concurrent": "Số lần tải đồng thời tối đa",
    "settings.restart_hint": "⚠  Thay đổi có hiệu lực sau khi khởi động lại ứng dụng.",
    "settings.max_retries": "Số lần thử lại khi lỗi",
    "settings.embed_thumbnail": "Nhúng ảnh thu nhỏ (thumbnail)",
    "settings.embed_metadata": "Nhúng thông tin metadata",
    "settings.theme": "Giao diện màu sắc",
    "settings.language": "Ngôn ngữ",
    "settings.language_hint": (
        "Áp dụng ngay cho giao diện chính; các màn hình còn lại đổi sau khi khởi động lại."
    ),
    "settings.language_changed": "Đã đổi ngôn ngữ sang {language}",
    "settings.clipboard_watch": "Tự động phát hiện URL từ clipboard",
    "settings.clipboard_hint": "Kiểm tra clipboard mỗi 1,5 giây và tự động điền URL vào thanh tìm kiếm",
    "settings.debug_logging": "Ghi log chi tiết (debug)",
    "settings.debug_hint": "Ghi chi tiết vào omnidl_debug.log  •  Không cần khởi động lại",
    "settings.folder_not_found": "Không tìm thấy thư mục. Hãy thử chọn lại.",
    "settings.folder_create_failed": "Không thể tạo thư mục: {err}",
    "settings.select_folder": "Chọn thư mục tải xuống",
    # ── History tab ───────────────────────────────────────────────────────
    "history.title": "Lịch sử tải xuống",
    "history.clear_all": "Xóa tất cả",
    "history.search_placeholder": "Tìm theo tiêu đề, URL hoặc tên file...",
    "history.filter.all": "Tất cả",
    "history.status.completed": "Hoàn tất",
    "history.status.failed": "Lỗi",
    "history.status.cancelled": "Đã hủy",
    "history.load_more": "Tải thêm ({remaining} mục còn lại)",
    "history.empty_no_results": "Không tìm thấy kết quả",
    "history.empty_no_history": "Chưa có lịch sử tải xuống",
    "history.redownload": "Tải lại",
    "history.redownload_tip": "Tải xuống lại",
    "history.copy_url": "Sao chép URL",
    "history.copy_url_tip": "Sao chép URL",
    "history.folder": "Thư mục",
    "history.folder_tip": "Mở thư mục chứa file",
    "history.rename": "Đổi tên",
    "history.rename_tip": "Đổi tên file",
    "history.delete": "Xóa",
    "history.delete_tip": "Xóa khỏi lịch sử",
    "history.clear_confirm": "Xóa toàn bộ lịch sử tải xuống?",
    # ── Special downloads tab ────────────────────────────────────────────────
    "special.title": "Tải xuống đặc biệt",
    "special.subtitle": "Tải nội dung mà yt-dlp không hỗ trợ — Facebook Story v.v.",
    "special.platform_label": "Nền tảng:",
    "special.browser_label": "Trình duyệt:",
    "special.url_placeholder.facebook_story": "https://www.facebook.com/stories/...",
    "special.guide.facebook_story": (
        "Hướng dẫn:\n"
        "1. Đóng hoàn toàn Brave / Chrome (kể cả System Tray / Dock)\n"
        "2. Dán URL Story vào ô trên\n"
        "3. Chọn trình duyệt → nhấn  Tải về\n"
        "4. Trình duyệt sẽ tự mở, tải xong tự động\n"
        "Story hết hạn sau 24 giờ\n"
        "Dùng bản cài từ website — App Store không hỗ trợ"
    ),
    "special.download_btn": "Tải về",
    "special.downloading": "Đang tải...",
    "special.status.waiting": "Đang chờ...",
    "special.status.starting": "Đang khởi động...",
    "special.retry": "Thử lại",
    "special.clear_history": "Xóa lịch sử",
    "special.open_folder": "Mở thư mục",
    "special.view": "Xem",
    "special.error.paste_url": "Hãy dán URL vào ô trên.",
    "special.error.not_story_url": (
        "Link này không phải Facebook Story.\n"
        "Story có dạng facebook.com/stories/... — link fb.watch hoặc video thường "
        "hãy tải ở tab Tải xuống."
    ),
    "special.downloaded": "Đã tải: {name}",
    "special.no_result": "Không có kết quả.",
    "special.error_prefix": "Lỗi: {msg}",
    "special.convert_unavailable": "Convert service không khả dụng.",
    "special.converting": "Đang chuyển đổi → .{ext}…",
    "special.convert_done": "Convert xong: {name}",
    "special.sent_to": "Đã gửi → {node}",
    "special.send_failed": "Gửi thất bại → {node}: {err}",
    "special.sending": "Đang gửi đến: {nodes}",
    "special.send_error": "Lỗi gửi: {err}",
    "special.file_deleted": "File đã được xóa.",
    # ── Archive tab ───────────────────────────────────────────────────────
    "archive.title": "Nén / Giải nén",
    "archive.mode.compress": "Nén",
    "archive.mode.extract": "Giải nén",
    "archive.cancel": "Hủy",
    "archive.add_file": "Thêm file",
    "archive.add_folder": "Thêm thư mục",
    "archive.remove_selected": "Xóa mục chọn",
    "archive.format_label": "Định dạng:",
    "archive.compress_individually": "Nén từng file riêng",
    "archive.encrypt_names": "Mã hóa tên file (chỉ 7z)",
    "archive.password_label": "Mật khẩu:",
    "archive.password_placeholder": "Để trống nếu không đặt mật khẩu",
    "archive.name_label": "Tên archive:",
    "archive.use_orig_name": "Dùng tên gốc",
    "archive.use_orig_name_tip": (
        'Chỉ dùng được khi chọn đúng 1 file/thư mục để nén và không bật "Nén từng file riêng"'
    ),
    "archive.save_dir_label": "Thư mục lưu:",
    "archive.choose": "Chọn...",
    "archive.file_label": "File archive:",
    "archive.extract_dest_label": "Thư mục giải nén:",
    "archive.view_contents": "Xem nội dung",
    "archive.no_file_title": "Chưa chọn file",
    "archive.no_file_compress_msg": "Hãy thêm ít nhất một file hoặc thư mục để nén.",
    "archive.no_file_extract_msg": "Hãy chọn file archive để giải nén.",
    "archive.no_file_list_msg": "Hãy chọn file archive.",
    "archive.status.compressing": "Đang nén...",
    "archive.status.extracting": "Đang giải nén...",
    "archive.status.listing": "Đang đọc nội dung...",
    "archive.status.compress_done": "Đã nén {count} archive vào {dir}",
    "archive.status.compress_cancelled": "Đã hủy nén",
    "archive.status.compress_failed": "Nén thất bại",
    "archive.status.extract_done": "Đã giải nén {count} file ({size}) vào {dir}",
    "archive.status.extract_cancelled": "Đã hủy giải nén",
    "archive.status.extract_failed": "Giải nén thất bại",
    "archive.status.list_count": "{count} mục trong archive",
    "archive.status.list_failed": "Không đọc được nội dung",
    "archive.done_title": "Hoàn tất",
    "archive.compress_done_msg": "Đã tạo {count} archive.\nMở thư mục chứa file?",
    "archive.extract_done_msg": "Đã giải nén xong.\nMở thư mục?",
    "archive.error_compress_title": "Lỗi nén",
    "archive.error_extract_title": "Lỗi giải nén",
    "archive.error_title": "Lỗi",
    "archive.choose_file": "Chọn file",
    "archive.choose_folder": "Chọn thư mục",
    "archive.choose_save_folder": "Chọn thư mục lưu",
    "archive.choose_archive_file": "Chọn file archive",
    "archive.choose_extract_folder": "Chọn thư mục giải nén",
    # ── Batch tab ─────────────────────────────────────────────────────────
    "batch.title": "Tải xuống hàng loạt",
    "batch.hint": "Dán URL vào đây — mỗi dòng một link (tối đa {max})",
    "batch.import_txt": "Nhập .txt",
    "batch.clear_all": "Xóa tất cả",
    "batch.results_title": "Kết quả phân tích",
    "batch.select_all": "Chọn hết",
    "batch.sequential": "Tải tuần tự",
    "batch.quality_label": "Chất lượng:",
    "batch.retry_errors": "Thử lại lỗi",
    "batch.retry_count": "Thử lại {count} lỗi",
    "batch.queue_all": "Thêm tất cả",
    "batch.queue_count": "Queue {count} video",
    "batch.queue_add_count": "Thêm {count} video",
    "batch.empty": "Chưa có URL nào — dán link ở trên hoặc import file .txt",
    "batch.analysing_dots": "Đang phân tích…",
    "batch.analyse_again": "Phân tích lại",
    "batch.retrying_dots": "Đang thử lại…",
    "batch.no_valid_urls": "Không có URL nào hợp lệ ({errors} lỗi)",
    "batch.ready_summary": "✓  {ready}/{total} sẵn sàng{err_note}",
    "batch.ready_err_note": "  ·  {errors} lỗi",
    "batch.retrying_urls": "Đang thử lại {count} URL lỗi…",
    "batch.analysis_failed": "Phân tích thất bại",
    "batch.import_dialog_title": "Nhập danh sách URL",
    "batch.import_read_error": "Không đọc được file: {err}",
    "batch.import_no_valid": "Không tìm thấy URL hợp lệ trong file.",
    "batch.import_done": "Đã nhập {count} URL",
    "batch.import_capped": "(giới hạn {max}, bỏ qua {skipped})",
    "batch.import_invalid": "· {count} dòng không hợp lệ bỏ qua",
    "batch.url_count": "{count} URL",
    "batch.url_count_capped": "{count} URL — chỉ lấy {max} đầu tiên",
    "batch.queued_toast": "Đã thêm {count} video vào queue.",
    "batch.queue_added": "✓  Đã thêm vào queue",
    "batch.queue_added_summary": "✓  {count} video đã được thêm vào queue",
    "batch.downloading_sequential": "⬇  Đang tải tuần tự…",
    "batch.sequential_status": "Tải tuần tự: {count} video",
    "batch.sequential_toast": "Đang tải tuần tự — còn {remaining} video.",
    "batch.sequential_done_btn": "✓  Đã tải xong",
    "batch.sequential_done_status": "✓  Hoàn tất tải tuần tự",
    "batch.playlist_label_default": "danh sách phát",
    "batch.playlist_loaded": "Đã tải {count} video từ {label} vào Batch.",
    "batch.queue_all_failed": "Không thêm được video nào vào queue ({count} lỗi)",
    "batch.cancel_analyse": "Dừng phân tích",
    "batch.analyse_stopped": "Đã dừng phân tích",
    "batch.analysing_progress": "Đang phân tích {done}/{total}…",
    # ── Editor tab ────────────────────────────────────────────────────────
    "editor.empty": "Chưa có video. Mở file hoặc nhấn 'Chỉnh sửa' từ tab Hàng đợi.",
    "editor.hide_panel": "⊡ Ẩn bảng",
    "editor.show_panel": "⊞ Hiện bảng",
    "editor.open_file": "📂  Mở file",
    "editor.close_file": "✕  Đóng file",
    "editor.close_file_tip": "Đóng file hiện tại",
    "editor.set_in": "[ Đặt điểm vào",
    "editor.set_out": "Đặt điểm ra ]",
    "editor.in_label": "Điểm vào: {time}",
    "editor.out_label": "Điểm ra: {time}",
    "editor.out_placeholder": "Điểm ra: --:--",
    "editor.rotate_label": "Xoay:",
    "editor.rotate.none": "Không xoay",
    "editor.rotate.cw90": "90° thuận chiều kim đồng hồ",
    "editor.rotate.ccw90": "90° ngược chiều kim đồng hồ",
    "editor.rotate.180": "180°",
    "editor.mute": "Tắt tiếng",
    "editor.preview_btn": "▶ Xem thử",
    "editor.export_btn": "✂  Xuất",
    "editor.speed_label": "Tốc độ:",
    "editor.volume_label": "Âm lượng:",
    "editor.text_label": "Văn bản:",
    "editor.text_placeholder": "Văn bản chồng lên video...",
    "editor.position_label": "Vị trí:",
    "editor.pos.top": "Trên",
    "editor.pos.middle": "Giữa",
    "editor.pos.bottom": "Dưới",
    "editor.size_label": "Cỡ:",
    "editor.color_label": "Màu:",
    "editor.color.white": "Trắng",
    "editor.color.black": "Đen",
    "editor.color.yellow": "Vàng",
    "editor.color.red": "Đỏ",
    "editor.box": "Nền",
    "editor.shadow": "Bóng",
    "editor.effects_title": "Hiệu ứng video",
    "editor.hide": "- Ẩn",
    "editor.show": "+ Hiển thị",
    "editor.brightness": "Sáng:",
    "editor.contrast": "Tương phản:",
    "editor.saturation": "Bão hòa:",
    "editor.hue": "Màu (hue):",
    "editor.blur": "Mờ (blur):",
    "editor.fade_in": "Mờ dần vào:",
    "editor.fade_out": "Mờ dần ra:",
    "editor.creating_preview_btn": "⏳ Đang tạo...",
    "editor.creating_preview_status": "Đang tạo xem thử...",
    "editor.back_to_original": "← Gốc",
    "editor.previewing_status": "Đang xem thử (10s)",
    "editor.preview_error": "Lỗi xem thử: {msg}",
    "editor.export_cancel_btn": "Huỷ",
    "editor.exporting_status": "Đang xuất...",
    "editor.in_after_out_error": "In phải nhỏ hơn Out",
    "editor.export_saved": "Đã lưu: {name}",
    "editor.export_error": "Lỗi: {msg}",
    "editor.choose_video_file": "Chọn file video",
    "editor.all_files_filter": "Tất cả",
    # ── Live monitor tab ──────────────────────────────────────────────────
    "live.title": "Theo dõi trực tiếp",
    "live.hint": "Dán URL live stream — Instagram, YouTube, TikTok, Facebook, Twitch…  (tối đa {max})",
    "live.add_btn": "Thêm",
    "live.check_interval_label": "Kiểm tra mới:",
    "live.seconds_label": "giây",
    "live.pause": "Tạm ngưng",
    "live.resume": "Tiếp tục",
    "live.empty": "Chưa có URL nào được theo dõi\nDán URL live stream ở trên để bắt đầu tự động ghi",
    "live.state.waiting": "Chờ live",
    "live.state.checking": "Đang kiểm tra…",
    "live.state.live": "Đang LIVE",
    "live.state.recording": "Đang ghi",
    "live.state.ended": "Đã ghi xong",
    "live.check_now": "Kiểm tra",
    "live.open": "Mở",
    "live.convert_btn": "Chuyển",
    "live.invalid_url": "URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://",
    "live.limit_reached": "Đã đạt giới hạn {max} URL.",
    "live.already_watching": "URL này đang được theo dõi.",
    "live.ig_cookie_needed": (
        "Profile watcher cần cookie file Instagram.\nCấu hình trong Settings → Network → Cookie file."
    ),
    "live.watching_toast": "Đang theo dõi {label} — sẽ tự ghi khi live bắt đầu.",
    "live.profile_watch_suffix": "(theo dõi profile)",
    "live.paused_status": "Đã tạm ngưng",
    "live.rate_limited": "Rate limited — thử lại sau {wait}s",
    "live.fail_hint": "  (lỗi {failed}/{max})",
    "live.recheck_in": "Kiểm tra lại sau {remaining}s{fail_hint}",
    "live.new_check_in": "Kiểm tra mới {interval}s",
    "live.checking_stream": "Đang kiểm tra stream…",
    "live.stream_live_starting": "Stream đang phát — đang khởi động ghi…",
    "live.recording_dots": "Đang ghi…",
    "live.cookie_old": "Cookie cũ {age} ngày — có thể bị lỗi auth",
    "live.cookie_banner": (
        "Cookie file đã {age} ngày tuổi — Instagram/TikTok Live có thể thất bại. "
        "Refresh cookie trong Settings → Network."
    ),
    "live.check_stuck_error": "Kiểm tra bị treo {elapsed}s. Thử lại hoặc kiểm tra kết nối mạng.",
    "live.download_failed": "Tải xuống thất bại",
    "live.file_not_found": "File .ts khong tim thay.",
    "live.retry_failure_error": (
        "Da thu {failures} lan that bai. Loi cuoi: {err}\n"
        "Kiem tra cookie Instagram hoac ket noi mang, roi nhan x va them lai URL."
    ),
    "live.status_recording": "{count} đang ghi",
    "live.status_waiting": "{count} đang chờ",
    "live.status_ended": "{count} đã xong",
    # ── Convert tab ───────────────────────────────────────────────────────
    "convert.quality.high.label": "Chất lượng cao",
    "convert.quality.high.desc": "H.264 CRF 18 · AAC 192k · Giữ độ phân giải",
    "convert.quality.standard.label": "Chuẩn",
    "convert.quality.standard.desc": "H.264 CRF 23 · AAC 128k · Phù hợp mọi iPhone",
    "convert.quality.small.label": "File nhỏ",
    "convert.quality.small.desc": "H.264 CRF 28 · AAC 96k · Tối đa 720p",
    "convert.quality.custom.label": "Tùy chỉnh",
    "convert.quality.custom.desc": "Giá trị CRF/CQ tùy chọn (16-35)",
    "convert.state.pending": "Chờ",
    "convert.state.queued": "Hàng chờ",
    "convert.state.converting": "Đang chuyển…",
    "convert.state.done": "✓ Xong",
    "convert.state.failed": "✕ Lỗi",
    "convert.card.delete_output": "Xóa file",
    "convert.card.output_gone": "→ {name} (đã xóa khỏi ổ đĩa)",
    "convert.card.before": "Trước",
    "convert.card.after": "Sau",
    "convert.card.reading": "Đang đọc…",
    "convert.card.reading_info": "Đang đọc thông tin…",
    "convert.clear_done": "Xóa xong/lỗi",
    "convert.select_all": "Chọn tất cả",
    "convert.select_all_tip": "Chọn / bỏ chọn mọi file đang chờ.",
    "convert.select_file_tip": "Chọn file này để chuyển đổi.",
    "convert.parallel_label": "Chạy song song:",
    "convert.parallel_tip": "Số file được chuyển đổi cùng lúc (1-8). Càng cao càng nhanh nhưng tốn CPU hơn.",
    "convert.settings_toggle_up": "⚙ Thông số  ▲",
    "convert.settings_toggle_down": "⚙ Thông số  ▼",
    "convert.quality_label": "Chất lượng",
    "convert.custom_value_label": "Gia tri (16-35):",
    "convert.encoder_label": "Bộ mã hoá",
    "convert.speed_label": "Tốc độ",
    "convert.codec_label": "Codec",
    "convert.subtitle_label": "Phụ đề",
    "convert.gen_subs_check": "Tạo phụ đề tự động (.srt)",
    "convert.rating_label": "Đánh giá",
    "convert.vmaf_check": "Chấm điểm VMAF so với file gốc",
    "convert.vmaf_tip": (
        "So sánh video sau khi chuyển đổi với file gốc bằng VMAF (0-100).\n"
        "Điểm càng cao càng giống bản gốc. Việc chấm điểm giải mã lại cả hai file nên tốn thêm thời gian."
    ),
    "convert.empty_title": "Chưa có file nào",
    "convert.empty_hint": 'Nhấn "Thêm file" hoặc "Thêm thư mục" để chọn video',
    "convert.empty_formats": "Hỗ trợ: MP4, MKV, WebM, AVI, MOV, FLV, WMV, TS, 3GP…",
    "convert.generate_subs_btn": "Tạo phụ đề",
    "convert.generate_subs_tip": "Chỉ tạo file .srt từ giọng nói trong video, không encode lại video.",
    "convert.convert_all_btn": "Chuyển đổi tất cả",
    "convert.no_whisper_tip": (
        "Bản FFmpeg đang dùng không có bộ lọc whisper.\n"
        "Cần bản FFmpeg 'full' (xem README) để tạo phụ đề tự động."
    ),
    "convert.first_enable_tip": "Lần đầu bật sẽ tải model nhận dạng giọng nói (~141 MB), chỉ tải một lần.",
    "convert.gpu_status": "GPU: {labels}",
    "convert.cpu_only_status": "Chỉ CPU",
    "convert.choose_videos_title": "Chọn video để chuyển đổi",
    "convert.choose_folder_title": "Chọn thư mục chứa video",
    "convert.scanning_folder": "Đang quét thư mục…",
    "convert.no_video_in_folder": "Không tìm thấy video trong {folder}",
    "convert.added_from_folder": "Đã thêm {count} file từ {folder}",
    "convert.subtitle_created": "Đã tạo phụ đề: {name}",
    "convert.status.processing": "Đang xử lý {count}",
    "convert.status.waiting_count": "{count} chờ",
    "convert.status.total_files": " / {total} file",
    "convert.status.complete": "Hoàn tất {done}/{total} file ✓",
    "convert.status.done_count": "{count} xong",
    "convert.status.failed_count": "{count} lỗi",
    "convert.delete_output_title": "Xóa file đã convert",
    "convert.delete_output_msg": (
        "Bạn có chắc muốn xóa file đã convert trên laptop không?\n\n"
        "{name}\n\n"
        "Hãy chắc chắn file đã được lưu trên iPhone trước khi xóa.\n"
        "Thao tác này không thể hoàn tác."
    ),
    "convert.delete_error_title": "Lỗi xóa file",
    "convert.delete_error_msg": "Không thể xóa file:\n{err}",
    "convert.taildrop_sent": "Đã gửi '{name}' đến {node}",
    "convert.taildrop_failed": "Taildrop thất bại '{name}': {err}",
    # ── Settings: Taildrop panel ─────────────────────────────────────────
    "settings.taildrop.description": (
        "Sau khi tải xong, tự động gửi file sang iPhone qua Taildrop (Tailscale).\n"
        "Yêu cầu: Tailscale CLI trên PC và Taildrop bật trên iPhone.\n"
        "File xuất hiện trong ứng dụng Files của iOS."
    ),
    "settings.taildrop.enable": "Bật Taildrop",
    "settings.taildrop.cli_found": "✅  tailscale CLI phát hiện trên PATH",
    "settings.taildrop.cli_not_found": "⚠️  Không tìm thấy tailscale CLI — cài Tailscale trên PC này",
    "settings.taildrop.mode_header": "📤  Chế độ gửi file",
    "settings.taildrop.mode_auto_btn": "🔄  Tự động",
    "settings.taildrop.mode_manual_btn": "🖱️  Thủ công",
    "settings.taildrop.mode_auto_desc": "⚡ File sẽ tự động gửi sang iPhone ngay sau mỗi lần tải xong.",
    "settings.taildrop.mode_manual_desc": (
        "🖱️ File chỉ được gửi khi bạn nhấn Transfer thủ công trong Remote UI."
    ),
    "settings.taildrop.nodes_header": "📱  Thiết bị đích (chọn một hoặc nhiều máy)",
    "settings.taildrop.nodes_hint": "Nhấn 🔍 Tìm thiết bị để quét. Tích chọn máy muốn gửi file.",
    "settings.taildrop.no_nodes": "Chưa có thiết bị nào. Nhấn 🔍 để quét.",
    "settings.taildrop.manual_label": "Hoặc nhập tên node thủ công:",
    "settings.taildrop.manual_placeholder": "vd: iphone hoặc 100.64.x.x",
    "settings.taildrop.add_btn": "➕ Thêm",
    "settings.taildrop.scan_btn": "🔍  Tìm thiết bị Tailscale",
    "settings.taildrop.toggle_on": "📲  Taildrop bật.",
    "settings.taildrop.toggle_off": "📲  Taildrop tắt.",
    "settings.taildrop.mode_auto_label": "Tự động — gửi ngay sau mỗi lần tải xong",
    "settings.taildrop.mode_manual_label": "Thủ công — chỉ gửi khi bạn yêu cầu",
    "settings.taildrop.mode_toast": "📤  Chế độ Taildrop: {label}.",
    "settings.taildrop.invalid_node": (
        "❌  Tên node không hợp lệ — chỉ chứa chữ, số, dấu gạch ngang, dấu chấm."
    ),
    "settings.taildrop.node_added": "➕  Đã thêm node: {node}",
    "settings.taildrop.scanning": "⏳  Đang quét...",
    "settings.taildrop.no_devices_found": (
        "⚠️  Không tìm thấy thiết bị nào khác online.\n→ Mở app Tailscale trên iPhone và đảm bảo đang kết nối."
    ),
    "settings.taildrop.devices_found": "✅  Tìm thấy {count} thiết bị. Tích chọn máy muốn gửi.",
    # ── Settings: Tools panel ────────────────────────────────────────────
    "settings.tools.section.ytdlp": "ENGINE YT-DLP",
    "settings.tools.section.gallery_dl": "ENGINE GALLERY-DL",
    "settings.tools.section.data_privacy": "DỮ LIỆU & QUYỀN RIÊNG TƯ",
    "settings.tools.version_label": "Phiên bản",
    "settings.tools.update_ytdlp_btn": "Cập nhật yt-dlp",
    "settings.tools.checking_updates": "Đang kiểm tra cập nhật…",
    "settings.tools.updating_toast": "Đang cập nhật {tool}, vui lòng chờ…",
    "settings.tools.updated_status": "Đã cập nhật → {ver}",
    "settings.tools.updated_toast": "{tool} đã cập nhật lên {ver}",
    "settings.tools.update_error_status": "Lỗi: {msg}",
    "settings.tools.update_failed_toast": "Cập nhật thất bại: {msg}",
    "settings.tools.keyring_btn": "Cài keyring (hỗ trợ Brave/Chrome 127+)",
    "settings.tools.keyring_not_installed": "⚠ Chưa cài — cần cho Brave/Chrome 127+",
    "settings.tools.keyring_warning": "⚠  Cần thiết nếu Brave/Chrome báo lỗi DPAPI khi lấy cookies.",
    "settings.tools.extra_args_label": "Tham số thêm",
    "settings.tools.extra_args_placeholder": "ví dụ: --no-playlist",
    "settings.tools.gallery_update_btn": "Cập nhật gallery-dl",
    "settings.tools.clear_data_desc": (
        "Xóa toàn bộ dữ liệu ứng dụng: lịch sử tải, thiết lập cấu hình\n"
        "và đường dẫn cookie file. File cookie trên đĩa không bị xóa.\n"
        "Không thể thực hiện khi đang có download/conversion đang chạy."
    ),
    "settings.tools.clear_data_btn": "🗑  Xóa tất cả dữ liệu",
    "settings.tools.keyring_bundled_suffix": " (đã tích hợp sẵn)",
    "settings.tools.keyring_bundled_toast": "keyring đã có sẵn trong bản build. Thử lại lấy cookies.",
    "settings.tools.keyring_missing_status": "keyring không có — tải lại phiên bản mới hơn",
    "settings.tools.keyring_missing_toast": (
        "keyring không tìm thấy trong build. Vui lòng tải phiên bản EXE mới nhất."
    ),
    "settings.tools.installing_keyring": "Đang cài keyring…",
    "settings.tools.keyring_installed_toast": "keyring đã cài. Thử lại lấy cookies từ Brave/Chrome.",
    "settings.tools.keyring_install_failed_toast": "Cài keyring thất bại: {msg}",
    "settings.tools.clear_data_running": "Không thể xóa dữ liệu khi {parts}.",
    "settings.tools.active_downloads": "{count} download đang chạy",
    "settings.tools.active_conversions": "{count} conversion đang chạy",
    "settings.tools.and_join": " và ",
    "settings.tools.confirm_clear_title": "OmniDL — Xác nhận xóa dữ liệu",
    "settings.tools.confirm_clear_msg": (
        "Thao tác này sẽ:\n"
        "  • Xóa toàn bộ lịch sử tải\n"
        "  • Đặt lại tất cả thiết lập về mặc định\n"
        "  • Xóa đường dẫn cookie file khỏi cấu hình\n\n"
        "File cookie và file đã tải sẽ không bị ảnh hưởng.\n\nTiếp tục?"
    ),
    "settings.tools.data_cleared_status": "✓ Đã xóa",
    "settings.tools.data_cleared_toast": "Đã xóa toàn bộ dữ liệu. Khởi động lại app để áp dụng đầy đủ.",
    "settings.tools.clear_failed_toast": "Xóa dữ liệu thất bại: {msg}",
    # ── Settings: Remote API panel ───────────────────────────────────────
    "settings.api.section.remote": "API TỪ XA",
    "settings.api.section.tailscale": "HỒ SƠ HTTPS TAILSCALE",
    "settings.api.description": (
        "Bật để điều khiển OmniDL từ xa qua mạng LAN (iPhone, Android).\n"
        "Server chạy trong luồng riêng, không ảnh hưởng download hiện tại.\n"
        "Chỉ bật khi cần — tắt khi không dùng để bảo mật thiết bị."
    ),
    "settings.api.enable": "Bật Remote API",
    "settings.api.token_header": "🔑  Bearer Token",
    "settings.api.copy_btn": "📋 Sao chép",
    "settings.api.rotate_btn": "🔄  Tạo token mới",
    "settings.api.ts_description": (
        "Truy cập Remote API qua HTTPS trên mạng Tailscale.\n"
        "OmniDL tự động chạy tailscale serve — không cần mở port tường lửa.\n"
        "Yêu cầu: Tailscale đã cài và đang nhập trên máy này."
    ),
    "settings.api.ts_enable": "Bật Tailscale HTTPS Profile",
    "settings.api.reset_profile_btn": "Đặt lại hồ sơ",
    "settings.api.no_token": "(chưa có token — bật API để tạo tự động)",
    "settings.api.running": "🟢  Đang chạy  —  http://<IP LAN>:{port}",
    "settings.api.enabled_not_started": "⚠️  Đã bật nhưng chưa khởi động (thiếu fastapi/uvicorn?)",
    "settings.api.disabled": "⚫  Đã tắt",
    "settings.api.enabled_toast": "✅  Remote API đã bật.",
    "settings.api.missing_deps": "⚠️  Cần cài fastapi & uvicorn trước: pip install -r requirements-api.txt",
    "settings.api.start_error": "Lỗi khởi động API: {err}",
    "settings.api.disabled_toast": "⚫  Remote API đã tắt.",
    "settings.api.no_token_toast": "Chưa có token. Hãy bật Remote API trước.",
    "settings.api.token_copied": "✅  Token đã sao chép vào clipboard.",
    "settings.api.rotate_dialog_title": "Tạo token mới",
    "settings.api.rotate_confirm_msg": (
        "Token hiện tại sẽ bị vô hiệu hóa.\nTất cả thiết bị đang kết nối cần cập nhật token mới.\n\nTiếp tục?"
    ),
    "settings.api.new_token_saved": "✅  Token mới đã lưu",
    "settings.api.restarting_toast": "🔄  Token mới đã tạo — đang khởi động lại server…",
    "settings.api.restarted_toast": "✅  Server đã khởi động lại với token mới.",
    "settings.api.restart_error": "Lỗi restart API: {err}",
    "settings.api.new_token_toast": "✅  Token mới đã tạo. Copy và cập nhật trên thiết bị.",
    "settings.api.ts_running": "Remote API đang chạy tại:\nhttps://{dns}",
    "settings.api.ts_internal_port": "Port nội bộ (ngẫu nhiên): {port}",
    "settings.api.ts_setting_up": "Đang thiết lập... (kiểm tra Tailscale đã kết nối chưa)",
    "settings.api.ts_internal_port_plain": "Port nội bộ: {port}",
    "settings.api.ts_off": "Đã tắt",
    "settings.api.enable_api_first": "Bật Remote API trước khi dùng Tailscale HTTPS Profile.",
    "settings.api.no_tailscale_cli": "Không tìm thấy tailscale CLI — cài Tailscale trên máy này.",
    "settings.api.setting_up_https": "Đang thiết lập Tailscale HTTPS Profile...",
    "settings.api.login_hint": " Tailscale chưa đăng nhập? Chạy 'tailscale login' rồi thử lại.",
    "settings.api.serve_failed": "tailscale serve thất bại.{hint}",
    "settings.api.https_enabled_toast": "HTTPS Profile đã bật: https://{dns}",
    "settings.api.no_dns_error": (
        "serve đã bật nhưng không lấy được DNS name. Kiểm tra Tailscale đã đăng nhập."
    ),
    "settings.api.setup_error": "Lỗi thiết lập HTTPS Profile: {err}",
    "settings.api.disabling_https": "Đang tắt Tailscale HTTPS Profile...",
    "settings.api.https_disabled_toast": "Tailscale HTTPS Profile đã tắt.",
    "settings.api.disable_error": "Lỗi tắt HTTPS Profile: {err}",
    "settings.api.enable_https_first": "Hãy bật HTTPS Profile trước khi reset.",
    "settings.api.reset_confirm_msg": (
        "Thao tác này sẽ:\n• Tạo lại Tailscale serve profile\n• Tạo token mới (thiết bị cần cập nhật)\n\n"
        "Tiếp tục?"
    ),
    "settings.api.resetting_status": "Đang reset...",
    "settings.api.resetting_toast": "Đang reset Tailscale HTTPS Profile...",
    "settings.api.reset_serve_failed": (
        "tailscale serve thất bại khi reset. Kiểm tra Tailscale đã đăng nhập."
    ),
    "settings.api.reset_success_dns": (
        "Profile đã reset: https://{dns}. Token mới đã tạo - cập nhật trên thiết bị."
    ),
    "settings.api.reset_success": "Profile đã reset. Token mới đã tạo - cập nhật trên thiết bị.",
    "settings.api.reset_error": "Lỗi reset Profile: {err}",
    # ── Settings: Network panel ──────────────────────────────────────────
    "settings.network.section.auth": "MẠNG & XÁC THỰC",
    "settings.network.proxy_label": "Proxy URL",
    "settings.network.browser_label": "Trình duyệt nguồn",
    "settings.network.use_cookies_label": "Dùng cookies",
    "settings.network.section.auto_extract": "LẤY COOKIES TỰ ĐỘNG",
    "settings.network.extract_global_btn": "🔄  Firefox / Edge / Opera",
    "settings.network.extract_cdp_btn": "🦁  Brave / Chrome 127+",
    "settings.network.extract_hint": (
        "🔄 = yt-dlp đọc trực tiếp (cần đóng Brave/Chrome trước)   •   "
        "🦁 = CDP — không cần đóng trình duyệt, Brave 127+ an toàn"
    ),
    "settings.network.section.manual_import": "IMPORT FILE THỦ CÔNG",
    "settings.network.fallback_hint": (
        "🌐  Cookie fallback — YouTube, Twitch, Vimeo...  "
        "(dùng khi nền tảng chưa có trong bảng Per-Platform bên dưới)"
    ),
    "settings.network.fallback_warning": (
        "⚠  File này chứa toàn bộ cookies của trình duyệt (Google, email, banking...).\n"
        "   Ưu tiên dùng bảng Per-Platform bên dưới để bảo mật hơn."
    ),
    "settings.network.browse_btn": "Duyệt…",
    "settings.network.clear_btn": "🗑 Xoá",
    "settings.network.section.per_platform": "COOKIE THEO NỀN TẢNG",
    "settings.network.recommended_badge": "KHUYẾN NGHỊ",
    "settings.network.per_platform_desc": (
        "✅ Ưu tiên dùng bảng này — mỗi file chỉ chứa cookies của đúng nền tảng đó.\n"
        "File TikTok không có cookies Google/email, file Instagram không có cookies banking.\n"
        "Nếu nền tảng có hàng riêng ở đây → KHÔNG cần dùng Cookie fallback bên trên."
    ),
    "settings.network.cdp_btn": "CDP",
    "settings.network.cdp_tip": "CDP — Brave/Chrome 127+ (không cần đóng trình duyệt)",
    "settings.network.ytdlp_btn": "yt-dlp",
    "settings.network.ytdlp_tip": "yt-dlp — Firefox / Edge / Opera (cần đóng Brave/Chrome trước)",
    "settings.network.choose_btn": "Chọn",
    "settings.network.choose_tip": "Chọn file cookie thủ công (.txt Netscape)",
    "settings.network.delete_tip": "Xóa cookie file của nền tảng này",
    "settings.network.extract_footer_hint": (
        "🔄 = yt-dlp (Firefox/Opera).  🦁 = CDP (Brave/Chrome 127+, không cần đóng trình duyệt)."
    ),
    "settings.network.section.tiktok_accounts": "TÀI KHOẢN TIKTOK",
    "settings.network.pool_badge": "KHO",
    "settings.network.tiktok_desc": (
        "Mỗi account được gán tối đa N slot tải đồng thời.\n"
        "Khi pool trống, app dùng 'Per-Platform TikTok cookie' ở trên."
    ),
    "settings.network.no_accounts": "Chưa có account nào. Nhấn '+ Thêm account' để thêm.",
    "settings.network.add_account_btn": "+ Thêm account",
    "settings.network.name_label": "Tên:",
    "settings.network.name_placeholder": "Tài khoản 1",
    "settings.network.no_cookie_chosen": "Chưa chọn cookie",
    "settings.network.save_btn": "Lưu",
    "settings.network.slots_tip": "So download toi da cung luc cho account nay",
    "settings.network.resume_btn": "Tiếp tục",
    "settings.network.pause_btn": "Tạm dừng",
    "settings.network.default_account_name": "Tài khoản",
    "settings.network.select_tiktok_cookie_title": "Chọn cookie file TikTok (Netscape format)",
    "settings.network.not_netscape_format": "File khong phai dinh dang Netscape cookie.",
    "settings.network.copy_failed": "Khong the sao chep file: {err}",
    "settings.network.cdp_unsupported_browser": "CDP chi ho tro Brave/Chrome/Edge.",
    "settings.network.starting_browser": "Dang khoi dong {browser}...",
    "settings.network.cdp_failed": "CDP that bai: {err}",
    "settings.network.cdp_got_tiktok": "CDP: da lay {count} cookies TikTok.",
    "settings.network.reading_tiktok_from": "Dang doc cookies TikTok tu {browser}...",
    "settings.network.extract_failed": "That bai: {err}",
    "settings.network.got_tiktok_cookies": "Da lay {count} cookies TikTok.",
    "settings.network.account_added": "Da them account '{name}'.",
    "settings.network.profile_label": "Hồ sơ:",
    "settings.network.profile_default": "Mặc định",
    "settings.network.profile_tip": (
        "Mỗi hồ sơ trình duyệt giữ một phiên đăng nhập riêng.\n"
        "Đăng nhập mỗi tài khoản TikTok vào một hồ sơ khác nhau, rồi thêm từng hồ sơ vào đây."
    ),
    "settings.network.slots_label": "Slot:",
    "settings.network.source_manual": "File thủ công",
    "settings.network.cookie_ready": "Đã sẵn sàng — {count} cookie, đã đăng nhập.",
    "settings.network.reject_missing": "Không tìm thấy file cookie vừa lấy.",
    "settings.network.reject_unreadable": "Không đọc được file cookie.",
    "settings.network.reject_not_logged_in": (
        "Hồ sơ này chưa đăng nhập TikTok. Hãy đăng nhập trên đúng hồ sơ đó rồi bấm lấy lại."
    ),
    "settings.network.reject_expired": (
        "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại trên trình duyệt rồi lấy lại cookie."
    ),
    "settings.network.duplicate_account": (
        "Đúng tài khoản TikTok này đã có trong pool ('{name}'). Hãy chọn hồ sơ trình duyệt khác."
    ),
    "settings.network.name_in_use": "Tên '{name}' đã được dùng.",
    "settings.network.rename_tip": "Bấm để đổi tên account",
    "settings.network.refresh_btn": "↻",
    "settings.network.refresh_tip": "Lấy lại cookie cho account này (dùng đúng trình duyệt/hồ sơ đã lưu)",
    "settings.network.refresh_no_source": (
        "Account này được thêm bằng file thủ công — hãy xóa rồi thêm lại bằng file mới."
    ),
    "settings.network.refreshing": "Đang làm mới cookie cho '{name}'...",
    "settings.network.account_refreshed": "Đã làm mới cookie cho '{name}'.",
    "settings.network.status_ok": "Hoạt động — còn khoảng {days} ngày",
    "settings.network.status_ok_session": "Hoạt động",
    "settings.network.status_paused": "Đang tạm dừng",
    "settings.network.status_missing": "Thiếu file cookie",
    "settings.network.status_unreadable": "Không đọc được cookie",
    "settings.network.status_not_logged_in": "Cookie chưa đăng nhập TikTok",
    "settings.network.status_expired": "Cookie đã hết hạn — bấm ↻ để lấy lại",
    "settings.network.pool_hint": (
        "Mẹo: mỗi tài khoản TikTok = một hồ sơ trình duyệt riêng. "
        "Tạo hồ sơ mới trong Brave/Chrome/Edge, đăng nhập tài khoản thứ hai ở đó, "
        "rồi quay lại đây chọn đúng hồ sơ — không cần đăng xuất tài khoản nào."
    ),
    "settings.network.account_rejected": (
        "Khong the them account — file cookie phai nam trong thu muc cookies cua ung dung."
    ),
    "settings.network.proxy_invalid": (
        "Proxy không hợp lệ — phải bắt đầu bằng http://, https://, socks4://, hoặc socks5://"
    ),
    "settings.network.select_cookies_title": "Chọn cookies.txt (định dạng Netscape)",
    "settings.network.file_not_found": "Không tìm thấy file.",
    "settings.network.not_netscape_full": (
        "File không phải định dạng Netscape cookie.\n"
        "Hãy chọn file cookies.txt được export từ trình duyệt hoặc tiện ích Cookie-Editor."
    ),
    "settings.network.copy_failed_full": "Không thể sao chép cookie file: {err}",
    "settings.network.cookie_saved_encrypted": "Cookie file đã được mã hóa và lưu vào thư mục an toàn.",
    "settings.network.no_file_selected": "Chưa chọn file",
    "settings.network.cdp_confirm_title": "OmniDL — Xác nhận lấy toàn bộ cookies (CDP)",
    "settings.network.cdp_confirm_msg": (
        "⚠ Thao tác này lấy TẤT CẢ cookies của Brave/Chrome,\n"
        "bao gồm cả Google, email, banking...\n\n"
        "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n"
        "Một port ngẫu nhiên trên localhost sẽ được mở trong ~10 giây.\n\n"
        "➡ Khuyến nghị: Dùng nút 🦁 ở từng platform bên dưới\n"
        "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
        "Tiếp tục lấy toàn bộ?"
    ),
    "settings.network.cdp_browser_unsupported": (
        "CDP chỉ hỗ trợ Brave/Chrome/Edge. Trình duyệt hiện tại: {browser}.\n"
        "Dùng nút 🔄 cho Firefox/Opera/Safari."
    ),
    "settings.network.error_status": "❌ {err}",
    "settings.network.cdp_global_failed_toast": "CDP thất bại: {err}",
    "settings.network.cdp_saved_status": "✓ {count} cookies đã lưu (CDP)",
    "settings.network.cdp_from_browser_toast": "CDP: đã lấy {count} cookies từ {browser}.",
    "settings.network.starting_cdp_status": "Đang khởi động {browser} (CDP)…",
    "settings.network.global_confirm_title": "OmniDL — Xác nhận lấy toàn bộ cookies",
    "settings.network.global_confirm_msg": (
        "⚠ Thao tác này lấy TẤT CẢ cookies của trình duyệt,\n"
        "bao gồm cả Google, email, banking...\n\n"
        "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n\n"
        "➡ Khuyến nghị: Dùng nút 🔄 / 🦁 ở từng platform bên dưới\n"
        "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
        "Tiếp tục lấy toàn bộ?"
    ),
    "settings.network.extract_failed_toast": "Lấy cookies thất bại: {err}",
    "settings.network.saved_status": "✓ {count} cookies đã lưu{note}",
    "settings.network.encrypted_note": " 🔒 (mã hóa DPAPI)",
    "settings.network.got_from_browser_toast": "Đã lấy {count} cookies từ {browser}{note}.",
    "settings.network.reading_from_browser_status": "Đang đọc cookies từ {browser}…",
    "settings.network.select_cookie_for": "Chọn cookie file cho {platform} (Netscape format)",
    "settings.network.file_not_found_vi": "File không tìm thấy.",
    "settings.network.copy_platform_failed": "Không thể sao chép cookie file: {err}",
    "settings.network.platform_cookie_saved": "Cookie {platform} đã được mã hóa và lưu vào thư mục an toàn.",
    "settings.network.platform_extract_failed_status": "❌ {platform}: {err}",
    "settings.network.platform_extract_failed_toast": "Lấy cookies {platform} thất bại: {err}",
    "settings.network.platform_saved_status": "✓ {platform}: {count} cookies đã lưu",
    "settings.network.platform_got_toast": "Đã lấy {count} cookies {platform} từ {browser}.",
    "settings.network.reading_platform_status": "Đang đọc cookies {platform} từ {browser}…",
    "settings.network.cdp_unsupported_use_browser": (
        "CDP chỉ hỗ trợ Brave/Chrome/Edge. Dùng 🔄 cho {browser}."
    ),
    "settings.network.platform_cdp_failed_status": "❌ {platform} CDP: {err}",
    "settings.network.platform_cdp_failed_toast": "CDP {platform} thất bại: {err}",
    "settings.network.platform_cdp_saved_status": "✓ {platform}: {count} cookies (CDP)",
    "settings.network.platform_cdp_toast": "CDP: đã lấy {count} cookies {platform}.",
    "settings.network.starting_cdp_platform_status": "Đang khởi động {browser} để lấy cookies {platform}…",
    # ── Added by the tab audit (home / cards / status bar) ───────────────
    "home.welcome_title": "Sẵn sàng tải xuống",
    "home.welcome_sub": "Dán link video vào thanh trên và nhấn Phân tích",
    "home.more_platforms": "+ 1000 nền tảng",
    "home.loading": "Đang tải thông tin media...",
    "home.loading_sub": "Có thể mất vài giây",
    "home.quality_section": "CHẤT LƯỢNG",
    "home.format_label": "Định dạng",
    "home.folder_label": "Thư mục",
    "home.browse": "Duyệt",
    "home.add_to_queue": "Thêm vào hàng đợi",
    "home.no_preview": "Không có xem trước",
    "home.choose_dir": "Chọn thư mục tải",
    "home.photo_note": "Ảnh — tải ở độ phân giải cao nhất",
    "home.no_media_info": "Không nhận được thông tin media.",
    "home.batch_unavailable": "Không mở được tab Hàng loạt.",
    "home.display_error": "Lỗi hiển thị: {err}",
    "home.added_toast": "Đã thêm: {title}",
    "home.quality.best": "Chất lượng cao nhất",
    "home.quality.4k": "4K / 2160p",
    "home.quality.1080": "1080p Full HD",
    "home.quality.720": "720p HD",
    "home.quality.480": "480p",
    "home.quality.360": "360p",
    "home.quality.audio_mp3": "Chỉ âm thanh MP3",
    "home.quality.audio_m4a": "Chỉ âm thanh M4A",
    "item.status.queued": "Chờ",
    "item.status.downloading": "Đang tải",
    "item.status.processing": "Xử lý",
    "item.status.paused": "Tạm dừng",
    "item.status.completed": "Hoàn tất",
    "item.status.failed": "Lỗi",
    "item.status.cancelled": "Đã hủy",
    "item.status.partial": "Lưu tạm",
    "item.status.unknown": "Không rõ",
    "item.pause_tip": "Tạm dừng / Tiếp tục",
    "item.cancel_tip": "Hủy tải xuống",
    "item.open": "Mở",
    "item.open_tip": "Mở thư mục chứa file",
    "item.preview": "Xem",
    "item.preview_tip": "Xem / phát file",
    "item.convert": "Chuyển",
    "item.convert_tip": "Chuyển đổi định dạng",
    "item.edit": "Sửa",
    "item.edit_tip": "Cắt / chỉnh sửa video",
    "item.send": "Gửi",
    "item.send_tip": "Gửi file qua Taildrop",
    "item.sending": "Đang gửi…",
    "item.rename": "Đổi tên",
    "item.rename_tip": "Đổi tên file",
    "pda.convert": "Chuyển đổi",
    "pda.convert_compact": "Chuyển",
    "pda.converting": "Đang chuyển → .{ext}...",
    "pda.converting_short": "Đang chuyển...",
    "pda.done": "Xong",
    "pda.send": "Gửi",
    "pda.sending": "Đang gửi...",
    "pda.delete": "Xoá",
    "pda.edit": "Sửa",
    "pda.convert_failed": "Chuyển đổi thất bại: {msg}",
    "pda.convert_error_file": "Lỗi chuyển đổi {name}: {err}",
    "pda.convert_error": "Lỗi chuyển đổi: {err}",
    "pda.empty_folder": "Thư mục rỗng.",
    "pda.pick_send_title": "Chọn file cần gửi",
    "pda.pick_send_confirm": "Gửi đã chọn",
    "pda.pick_delete_title": "Chọn file cần xoá",
    "pda.pick_delete_confirm": "Xoá đã chọn",
    "pda.confirm_delete_title": "Xác nhận xoá",
    "pda.confirm_delete_all": "Xoá tất cả {count} file của bài đăng này?",
    "pda.confirm_delete_empty_dir": "Xoá thư mục rỗng '{name}'?",
    "pda.confirm_delete_file": "Bạn có chắc muốn xoá file này?\n{name}",
    "pda.delete_failed": "Không xoá được: {err}",
    "pda.target_format": "Chọn định dạng đích:",
    "pda.fmt.custom": "Tuỳ chỉnh (chất lượng + encoder)...",
    "pda.encoder": "Bộ mã hoá:",
    "pda.quality": "Chất lượng:",
    "pda.crf": "CRF:",
    "pda.speed": "Tốc độ:",
    "pda.q.high": "Cao (CRF 18)",
    "pda.q.standard": "Chuẩn (CRF 23)",
    "pda.q.small": "Nhỏ 720p (CRF 28)",
    "pda.q.custom": "CRF tuỳ chỉnh",
    "pda.pick_files_label": "Chọn file cần thực hiện:",
    "pda.select_all": "Chọn tất cả",
    "pda.deselect_all": "Bỏ chọn",
    "status.idle": "Không có tác vụ",
    "status.downloading": "{count} đang tải",
    "status.net_ok": "Mạng OK",
    "status.net_down": "Mất kết nối",
    "status.eta_min": "còn ~{value} phút",
    "status.eta_sec": "còn ~{value}s",
    "palette.search": "Tìm kiếm lệnh...",
    "app.quit.downloads": "{count} lượt tải",
    "app.quit.conversions": "{count} lượt chuyển đổi",
    "app.quit.and": " và ",
    "app.quit.confirm": "{summary} đang chạy.\nĐóng và huỷ tất cả?",
    "settings.clipboard_on": "Đã bật theo dõi clipboard",
    "settings.clipboard_off": "Đã tắt theo dõi clipboard",
    "settings.debug_on": "Đã bật debug log — ghi vào omnidl_debug.log",
    "settings.debug_off": "Đã tắt debug log",
    "convert.cancelled": "Đã huỷ",
    "pda.fmt.mp4": "MP4 — H.264 / AAC (iPhone, Android)",
    "pda.fmt.mp3": "MP3 — Chỉ âm thanh",
    "pda.fmt.mkv": "MKV — Container không mất dữ liệu",
    "pda.fmt.avi": "AVI — Tương thích thiết bị cũ",
    "convert.codec.h264": "H.264 (tương thích cao nhất)",
    "convert.codec.hevc": "H.265 / HEVC (~30% nhỏ hơn)",
    "convert.codec.av1": "AV1 (~50% nhỏ hơn, cần ff8+)",
    "convert.speed.quality": "Chậm (Nén tốt nhất)",
    "convert.speed.balanced": "Cân bằng",
    "convert.speed.fast": "Nhanh (Nén ít hơn)",
    "convert.err.file_missing": "File không tồn tại: {path}",
    "convert.err.ffmpeg_exit": "ffmpeg thoát với lỗi {code}.\n{tail}",
    "trim.err.no_ffmpeg": "Không tìm thấy FFmpeg. Hãy cài FFmpeg và thử lại.",
    "subs.model.tiny": "Tiny (74 MB, nhanh nhất)",
    "subs.model.base": "Base (141 MB, cân bằng)",
    "subs.model.small": "Small (465 MB, chính xác hơn)",
    "subs.model.medium": "Medium (1.4 GB, tốt nhất)",
    "subs.lang.auto": "Tự động nhận diện",
    "subs.err.invalid_model": "Model không hợp lệ: {model}",
    "subs.err.invalid_language": "Ngôn ngữ không hợp lệ: {language}",
    "subs.err.no_whisper": "Bản FFmpeg đang dùng không có bộ lọc whisper — cần bản 'full' (xem README).",
    "subs.err.incomplete_download": "Tải model không đầy đủ ({got}/{total} bytes)",
    "subs.err.failed": "Tạo phụ đề thất bại: {err}",
    "subs.err.no_speech": "Không nhận diện được lời nói nào trong video.",
    # ── Engine / service messages ─────────────────────────────────────────
    # Errors and progress text raised outside the UI layer (download engines,
    # cookie extraction, live checkers).  Retry logic keys off the catalogue
    # KEY, never this text — see yt_dlp_engine._error_key().
    "err.private": "Nội dung ở chế độ riêng tư. Hãy bật cookie trong Cài đặt.",
    "err.not_found": "Không tìm thấy URL hoặc nội dung đã bị xóa.",
    "err.unsupported_platform": "yt-dlp chưa hỗ trợ nền tảng này.",
    "err.live_not_started": "Livestream chưa bắt đầu.",
    "err.not_currently_live": "Kênh hiện không phát trực tiếp.",
    "err.live_ended": "Livestream đã kết thúc.",
    "err.ig_photo_only": (
        "Bài đăng này chỉ có ảnh, không có video.\n"
        "OmniDL sẽ thử tải ảnh với format='best'.\n"
        "Nếu vẫn lỗi, hãy đảm bảo đang dùng cookie Instagram (không phải Facebook) và yt-dlp phiên bản mới "
        "nhất."
    ),
    "err.ytdlp_internal": (
        "yt-dlp gặp lỗi nội bộ khi phân tích URL này.\n"
        "Hãy cập nhật yt-dlp lên phiên bản mới nhất:\n"
        "Settings → Cập nhật yt-dlp, hoặc chạy: pip install -U yt-dlp"
    ),
    "err.ig_checkpoint": (
        "Instagram yêu cầu xác minh tài khoản.\n"
        "1. Mở Instagram trên trình duyệt, hoàn tất xác minh.\n"
        "2. Export cookies mới (dùng tiện ích 'Get cookies.txt LOCALLY').\n"
        "3. Cập nhật cookie file trong Settings → Network → Cookie file.\n"
        "Lưu ý: Cookie Instagram thường hết hạn sau 1–2 tuần."
    ),
    "err.rate_limit": (
        "Đã chạm giới hạn tần suất — quá nhiều yêu cầu trong thời gian ngắn.\n"
        "Chờ 5–10 phút rồi thử lại. Bật cookie trình duyệt trong Cài đặt có thể giúp ích."
    ),
    "err.tls_fingerprint": (
        "Lỗi kết nối TLS — máy chủ từ chối TLS fingerprint mặc định.\n"
        "OmniDL dùng curl_cffi (giả lập Chrome) để vượt qua lỗi này.\n"
        "Nếu lỗi vẫn xảy ra:\n"
        "  1. Kiểm tra antivirus/proxy không chặn HTTPS\n"
        "  2. Thử bật proxy trong Settings → Network → Proxy URL\n"
        "  3. Chạy: pip install -U curl-cffi"
    ),
    "err.fb_unavailable": (
        "Nội dung Facebook này không khả dụng. Có thể cần đăng nhập hoặc bị giới hạn theo khu vực."
    ),
    "err.geo_restricted": (
        "Nội dung này bị giới hạn theo khu vực và không khả dụng ở vùng của bạn.\n"
        "Thử bật VPN hoặc proxy trong Settings → Network → Proxy URL."
    ),
    "err.ffmpeg_livestream": (
        "Không thể ghi livestream — ffmpeg báo lỗi.\n"
        "Nguyên nhân thường gặp:\n"
        "  • Link livestream đã hết hạn (URL TikTok expire sau ~1–2 phút)\n"
        "    → Sao chép lại link và thử tải ngay lập tức\n"
        "  • Livestream đã kết thúc hoặc bị tạm dừng\n"
        "  • Kết nối mạng không ổn định trong quá trình ghi\n"
        "Nếu lỗi vẫn xảy ra: thử tải lại link hoặc đợi livestream ổn định."
    ),
    "err.ip_blocked": (
        "IP của bạn bị TikTok/nền tảng chặn truy cập bài đăng này.\n"
        "Nguyên nhân thường gặp:\n"
        "  • IP bị đưa vào danh sách đen do quá nhiều request (rate-limit tạm thời)\n"
        "  • ISP/VPS/datacenter IP bị chặn theo chính sách địa lý\n"
        "Giải pháp:\n"
        "  1. Bật proxy/VPN trong Settings → Network → Proxy URL\n"
        "     (ví dụ: socks5://127.0.0.1:1080 nếu dùng local proxy)\n"
        "  2. Chờ 5–15 phút rồi thử lại (nếu là rate-limit tạm thời)\n"
        "  3. Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok"
    ),
    "err.tiktok_login_required": (
        "TikTok yêu cầu đăng nhập để tải video này.\n"
        "Cookie pool có thể đã hết hạn hoặc dùng tài khoản khác.\n"
        "Giải pháp: Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok"
    ),
    "err.copyright": "Nội dung này bị chặn do khiếu nại bản quyền.",
    "err.blocked": (
        "Nội dung này bị chặn hoặc truy cập bị từ chối.\n"
        "Thử bật VPN hoặc proxy trong Settings → Network → Proxy URL."
    ),
    "err.account_suspended": "Tài khoản đăng nội dung này đã bị đình chỉ.",
    "err.members_only": (
        "Nội dung này chỉ dành cho thành viên/người đăng ký.\n"
        "Hãy chắc chắn bạn đã đăng nhập bằng cookie trong Cài đặt."
    ),
    "err.tiktok_10231": (
        "TikTok API từ chối request (status 10231) dù video vẫn xem được.\n"
        "Thử:\n"
        "  1. Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok\n"
        "  2. Bật proxy/VPN trong Settings → Network → Proxy URL"
    ),
    "err.video_deleted": (
        "Video này không còn tồn tại hoặc đã bị xóa.\n"
        "Kiểm tra lại URL — nếu link rút gọn (vt.tiktok.com), thử mở trong trình duyệt để lấy link đầy đủ."
    ),
    "err.threads_unsupported": (
        "Threads posts chưa được yt-dlp hỗ trợ.\n"
        "\n"
        "Cách tải video Threads:\n"
        "• Mở post trong trình duyệt → nhấn ... → Lưu\n"
        "• Hoặc dùng tiện ích 'Video Downloader' trên trình duyệt."
    ),
    "err.ig_stories_cookies": (
        "Instagram Stories cần cookie đăng nhập.\n"
        "Hãy thiết lập cookie file trong Settings → Network → Cookie file."
    ),
    "err.ig_live_cookies": (
        "Instagram Live cần cookie đăng nhập.\n"
        "Hãy thiết lập cookie file trong Settings → Network → Cookie file."
    ),
    "err.fb_live_cookies": (
        "Facebook Live cần cookie.\n"
        "Hãy thiết lập cookie file trong Settings → Network → Cookie file."
    ),
    "err.fb_stories_manual": (
        "Facebook Stories không thể tải tự động.\n"
        "\n"
        "Cách tải Story Facebook:\n"
        "• Mở Story trong trình duyệt → nhấn ... → Lưu video\n"
        "• Hoặc dùng tiện ích 'Video Downloader' trên trình duyệt."
    ),
    "err.ig_cookie_expired": "Cookie Instagram hết hạn — làm mới cookie trong Cài đặt.",
    "err.playlist_failed": "Không thể lấy danh sách từ URL này: {err}",
    "err.no_data": "Không nhận được dữ liệu từ URL. Kiểm tra lại URL hoặc thêm cookie file trong Cài đặt.",
    "err.playlist_empty": (
        "Playlist/profile không có video nào khả dụng.\n"
        "Có thể tài khoản private hoặc cần cookie file."
    ),
    "err.ffmpeg_not_found": "Không tìm thấy FFmpeg. Kiểm tra cài đặt FFmpeg.",
    "err.ffmpeg_stall": "FFmpeg stall watchdog: không có dữ liệu trong 120 giây — stream có thể đã kết thúc.",
    "err.no_error_detail": "Không có thông tin lỗi.",
    "err.hls_stall": (
        "Stall watchdog: curl_cffi HLS không có segment mới trong {seconds}s — stream có thể đã kết thúc."
    ),
    "err.livestream_ended_relink": (
        "Livestream đã kết thúc hoặc HLS URL không còn hợp lệ.\n"
        "Thêm lại link để theo dõi lần phát tiếp theo."
    ),
    "err.tiktok_audio_only": (
        "Video này chỉ có âm thanh — không có video track.\n"
        "TikTok product/showcase và \"template effect\" / AR effect videos không cung cấp video track qua API"
        " "
        "(chỉ expose audio stream).\n"
        "Cách tải: mở video trên TikTok app → chia sẻ → Lưu video."
    ),
    "err.tiktok_ec_blocked": (
        "Video này không thể tải — TikTok chặn hoàn toàn URL video.\n"
        "Đây là video E-Commerce/sản phẩm (isECVideo=1): TikTok không cung cấp video URL cho bất kỳ API "
        "client nào.\n"
        "Cách tải: mở video trên TikTok app → chia sẻ → Lưu video."
    ),
    "err.no_username_from_url": "Không thể lấy username từ URL.",
    "err.no_username_from_tiktok_url": "Không thể lấy username từ URL TikTok.",
    "err.invalid_url_scheme": "URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://",
    "err.monitor_limit": "Đã đạt giới hạn {count} URL.",
    "err.already_monitored": "URL này đang được theo dõi.",
    "err.profile_watch_needs_ig_cookie": (
        "Profile watcher cần cookie file Instagram. Cấu hình trong Settings → Network → Cookie file."
    ),
    "err.check_stuck": "Kiểm tra bị treo. Thử lại hoặc kiểm tra kết nối mạng.",
    "err.attempts_failed": "Đã thử {count} lần thất bại. Lỗi cuối: {err}",
    "err.download_failed": "Tải xuống thất bại",
    "err.network": "Lỗi kết nối mạng: {err}",
    "err.http": "Lỗi HTTP: {err}",
    "err.cookie_unreadable": "Không đọc được cookie file: {err}",
    "err.ig_cookie_required": (
        "Cần cookie file Instagram để kiểm tra live status.\n"
        "Cấu hình trong Settings → Network → Cookie file."
    ),
    "err.ig_cookie_no_sessionid": (
        "Cookie file không có sessionid Instagram.\n"
        "Export lại cookie file sau khi đăng nhập Instagram."
    ),
    "err.ig_cookie_invalid": (
        "Cookie Instagram đã hết hạn hoặc không hợp lệ.\n"
        "Refresh cookie file trong Settings → Network."
    ),
    "err.ig_account_not_found": "Tài khoản @{username} không tìm thấy.",
    "err.ig_rate_limited": "Rate limit — Instagram đang chặn tạm thời.\nChờ 5–10 phút rồi thử lại.",
    "err.ig_forbidden": "Truy cập bị từ chối (403). Cookie có thể đã hết hạn.",
    "err.ig_api_timeout": "Instagram API hết thời gian chờ. Thử lại sau.",
    "err.ig_bad_response": "Instagram trả về phản hồi không hợp lệ. Thử lại sau hoặc kiểm tra cookie file.",
    "err.tiktok_api_timeout": "TikTok API hết thời gian chờ. Thử lại sau.",
    "err.no_username_from_facebook_url": "Không thể lấy username từ URL Facebook.",
    "err.profile_watch_needs_fb_cookie": (
        "Theo dõi trang Facebook cần cookie file Facebook. Cấu hình trong Settings → Network → Cookie file."
    ),
    "err.fb_cookie_required": (
        "Cần cookie file Facebook để kiểm tra live status.\n"
        "Cấu hình trong Settings → Network → Cookie file."
    ),
    "err.fb_cookie_no_session": (
        "Cookie file không có phiên đăng nhập Facebook (thiếu c_user/xs).\n"
        "Export lại cookie file sau khi đăng nhập Facebook."
    ),
    "err.fb_cookie_invalid": (
        "Cookie Facebook đã hết hạn hoặc không hợp lệ.\n"
        "Refresh cookie file trong Settings → Network."
    ),
    "err.fb_page_not_found": "Không tìm thấy trang Facebook {username}.",
    "err.fb_rate_limited": "Rate limit — Facebook đang chặn tạm thời.\nChờ 5–10 phút rồi thử lại.",
    "err.fb_api_timeout": "Facebook hết thời gian chờ. Thử lại sau.",
    "progress.recorded": "⏺ {size} đã ghi",
    "cookie.err.app_bound_encryption": (
        "Brave/Chrome 127+ dùng App-Bound Encryption — không thể đọc cookie\n"
        "từ bên ngoài. Đây là giới hạn bảo mật của Windows/Chrome, không phải lỗi.\n"
        "\n"
        "✅ Cách nhanh nhất: Dùng Firefox\n"
        "   1. Mở Firefox, đăng nhập TikTok/Instagram/...\n"
        "   2. Đổi dropdown → firefox → bấm 🔄\n"
        "\n"
        "📁 Hoặc export thủ công từ Brave:\n"
        "   Cài tiện ích Cookie-Editor → Export → Netscape format\n"
        "   → Settings → Browse… → chọn file .txt vừa export"
    ),
    "cookie.err.brave_locked": (
        "Brave đang mở — database cookie bị khóa.\n"
        "⚠ Hãy đóng hoàn toàn Brave (kể cả background process trong System Tray)\n"
        "rồi bấm 🔄 lại. Sau khi lấy xong cookies có thể mở Brave lại."
    ),
    "cookie.err.db_missing_detected": (
        "Không tìm thấy database cookie của '{browser}'.\n"
        "⚠ Hãy chọn đúng trình duyệt bạn đang dùng trong dropdown\n"
        "'Cookie source browser' rồi bấm 🔄 lại."
    ),
    "cookie.err.db_missing": (
        "Không tìm thấy database cookie của trình duyệt đã chọn.\n"
        "⚠ Hãy chọn đúng trình duyệt bạn đang dùng trong dropdown\n"
        "'Cookie source browser' rồi bấm 🔄 lại."
    ),
    "cookie.err.db_locked": (
        "Không thể đọc cookies — trình duyệt đang mở và khóa database.\n"
        "Hãy đóng hoàn toàn trình duyệt (kể cả background process) rồi thử lại."
    ),
    "cookie.err.decrypt_failed": (
        "Không thể giải mã cookies từ trình duyệt.\n"
        "Thử chạy OmniDL với quyền Administrator, hoặc chọn trình duyệt khác."
    ),
    "cookie.err.profile_missing": (
        "Không tìm thấy profile của trình duyệt đã chọn.\n"
        "⚠ Kiểm tra lại dropdown 'Cookie source browser' — chọn đúng trình duyệt\n"
        "bạn đang dùng (ví dụ: Brave ≠ Chrome)."
    ),
    "cookie.err.permission_denied": (
        "Bị từ chối truy cập file cookie của trình duyệt.\n"
        "Thử chạy OmniDL với quyền Administrator."
    ),
    "cookie.err.browser_unsupported": (
        "Trình duyệt này chưa được yt-dlp hỗ trợ.\n"
        "Hãy thử Chrome, Firefox hoặc Edge."
    ),
    "cookie.err.cdp_no_connection": (
        "Không kết nối được vào trình duyệt.\n"
        "Thử lại — lần đầu có thể cần vài giây để khởi động."
    ),
    "cookie.err.cdp_websocket": (
        "Lỗi kết nối WebSocket với trình duyệt.\n"
        "Hãy thử lại hoặc khởi động lại OmniDL."
    ),
    "cookie.err.cdp_no_cookies": (
        "Không nhận được cookies từ trình duyệt. Hãy đăng nhập vào các trang rồi thử lại."
    ),
    "cookie.err.read_failed": (
        "Không thể đọc cookies — trình duyệt có thể chưa đăng nhập hoặc cơ sở dữ liệu bị khóa. Hãy thử đóng "
        "trình duyệt hoàn toàn."
    ),
    "cookie.err.browser_empty": (
        "Trình duyệt không có cookie nào. Hãy đăng nhập vào các trang bạn muốn tải trước."
    ),
    "cookie.err.platform_empty": (
        "Không tìm thấy cookie nào cho {platform} trong trình duyệt.\n"
        "Hãy đảm bảo đã đăng nhập vào {platform} trên trình duyệt đó."
    ),
    "cookie.err.platform_empty_browser": (
        "Không tìm thấy cookie {platform} trong {browser}.\n"
        "Hãy đảm bảo đã đăng nhập {platform} trên {browser}."
    ),
    "cookie.err.cdp_windows_only": (
        "Chế độ CDP (🦁) hiện chỉ hỗ trợ Windows.\n"
        "Trên macOS/Linux hãy dùng nút 🔄 (yt-dlp) hoặc chọn file cookie thủ công."
    ),
    "cookie.err.browser_not_installed": (
        "Không tìm thấy {browser} trên máy.\n"
        "Kiểm tra Brave/Chrome đã cài đặt chưa."
    ),
    "cookie.err.cdp_zero_cookies": (
        "Trình duyệt trả về 0 cookies.\n"
        "Hãy đăng nhập vào các trang trước khi lấy cookies."
    ),
    "cookie.err.cdp_timeout": (
        "Không kết nối được vào {browser} sau 20 giây.\n"
        "Hãy thử lại — lần đầu có thể cần chờ thêm."
    ),
    "cookie.err.browser_running": (
        "{browser} đang chạy — cần đóng tạm để đọc cookies.\n"
        "\n"
        "⚠ Hãy đóng hoàn toàn {browser} (kể cả System Tray),\n"
        "rồi bấm 🦁 lại. Sau khi lấy xong cookies có thể mở lại bình thường.\n"
        "\n"
        "Lý do: CDP cần đọc từ profile thật — profile đang bị {browser} giữ lock."
    ),
    "cookie.err.profile_dir_missing": (
        "Không tìm thấy thư mục profile của {browser}.\n"
        "Kiểm tra Brave/Chrome đã được cài và đăng nhập ít nhất 1 lần."
    ),
    "err.gdl_login_required": (
        "gallery-dl yêu cầu đăng nhập.\n"
        "Kiểm tra cookie file trong Settings → Network → Cookie file.\n"
        "Đảm bảo dùng cookie Instagram (không phải Facebook)."
    ),
    "err.gdl_rate_limited": (
        "gallery-dl bị rate limit — Instagram đang chặn tạm thời.\n"
        "Chờ 5–10 phút rồi thử lại."
    ),
    "err.gdl_not_installed_short": "gallery-dl chưa được cài đặt.\nChạy: pip install gallery-dl",
    "err.gdl_private": "Nội dung này ở chế độ riêng tư —\ncần cookie tài khoản có quyền xem.",
    "err.gdl_unknown": "gallery-dl thất bại không rõ nguyên nhân.",
    "err.gdl_not_installed": (
        "gallery-dl chưa được cài đặt.\n"
        "Chạy: pip install gallery-dl\n"
        "Sau đó khởi động lại OmniDL."
    ),
    "err.gdl_no_content": (
        "gallery-dl không tìm thấy nội dung tại URL này.\n"
        "Kiểm tra URL hoặc thử refresh cookie."
    ),
    "err.fb_post_advert_only": (
        "Bài viết Facebook này chỉ chứa ảnh.\n"
        "Video duy nhất yt-dlp thấy là quảng cáo Facebook chèn vào trang, không phải nội dung của bài viết.\n"
        "Hãy refresh cookie Facebook trong Cài đặt → Mạng rồi thử lại."
    ),
    "err.gdl_timeout": "gallery-dl hết thời gian khi lấy thông tin URL.",
    "err.gdl_missing_binary": "Không tìm thấy gallery-dl.\nCài đặt: pip install gallery-dl",
    "gdl.photo_count": "{count} ảnh",
    "progress.gdl_preparing": "⬇ Đang chuẩn bị tải ảnh…",
    "progress.gdl_video_audio": "⬇ Đang tải video có âm thanh…",
    "progress.gdl_downloaded": "⬇ {count} file đã tải",
    "err.cdn_file_too_small": "File tải về quá nhỏ — CDN link có thể đã hết hạn.",
    "err.cdn_link_expired": "CDN link đã hết hạn (chữ ký oe= hết hạn). Dán link mới từ trình duyệt.",
    "err.cdn_forbidden": "CDN link bị từ chối truy cập (403).",
    "err.ks_strategy_e_platform": (
        "Kuaishou: phương thức dự phòng cuối cùng (Strategy E) chỉ hỗ trợ Windows và macOS.\n"
        "\n"
        "Thử cấu hình cookie Kuaishou trong Settings → Network để kích hoạt các phương thức trích xuất khác."
    ),
    "err.ks_all_strategies_failed": (
        "Kuaishou: tất cả phương thức trích xuất đều thất bại.\n"
        "\n"
        "Nguyên nhân có thể:\n"
        "• Video đã bị xóa hoặc là private\n"
        "• Kuaishou chặn request từ IP hiện tại\n"
        "• Cấu trúc trang Kuaishou đã thay đổi\n"
        "\n"
        "Thử cấu hình cookie Kuaishou trong Settings → Network để kích hoạt thêm phương thức trích xuất."
    ),
    "err.ks_no_video_url": (
        "Kuaishou: không tìm thấy URL video trong dữ liệu trang.\n"
        "Video có thể bị giới hạn khu vực hoặc API đã thay đổi."
    ),
    "err.ks_bad_file": (
        "File tải về không hợp lệ (không phải MP4 hoặc quá nhỏ). URL CDN có thể đã hết hạn — thử lại."
    ),
    "err.ks_no_photo_id": "Không tách được photo_id từ URL: {url}\nKiểm tra lại URL Kuaishou.",
    "err.ks_cancelled": "Kuaishou: đã huỷ.",
    "err.ks_no_browser": (
        "Kuaishou: không tìm thấy Brave hoặc Chrome trên máy.\n"
        "\n"
        "Phương thức dự phòng cuối cùng (Strategy E) cần một trong hai trình duyệt này để mở trang Kuaishou "
        "và chặn link CDN thật.\n"
        "\n"
        "Hãy cài Brave (https://brave.com) hoặc Google Chrome rồi thử lại.\n"
        "Sau khi cài xong, không cần cấu hình gì thêm — OmniDL tự tìm."
    ),
    "err.ks_cdn_http": "Kuaishou CDN trả về HTTP {code}. URL CDN có thể đã hết hạn — thử lại.",
    "err.ks_cdn_http_after_reextract": (
        "Kuaishou CDN trả về HTTP {code} sau re-extract. URL CDN có thể đã hết hạn — thử lại."
    ),
    "err.ks_cdn_html_after_reextract": (
        "Kuaishou CDN vẫn trả về HTML sau re-extract — IP bị chặn hoặc video không còn khả dụng."
    ),
    "err.ks_cdn_html_no_page_url": (
        "Kuaishou CDN trả về HTML nhưng không thể xác định page URL để re-extract.\n"
        "Remote API cần truyền page URL Kuaishou, không phải CDN URL."
    ),
    "err.ks_no_page_url": (
        "Kuaishou: không thể xác định page URL để re-extract.\n"
        "task.url trông như CDN URL (video_id={video_id}) — Remote API cần truyền page URL, không phải CDN "
        "URL."
    ),
    "err.waaw_cdn_expired": (
        "Link CDN đã hết hạn hoặc bị khoá theo IP - hãy mở lại trang waaw.ac/f/... để lấy link mới"
    ),
    "err.waaw_platform": "waaw.ac engine yêu cầu Windows hoặc macOS.",
    "err.waaw_platform_linux": "waaw.ac engine yêu cầu Windows hoặc macOS.\nLinux chưa được hỗ trợ.",
    "err.waaw_file_too_small": "File tải về quá nhỏ (< 10 KB) — CDN có thể đã chặn.",
    "err.waaw_not_mp4": "File tải về không phải MP4 — CDN URL có thể đã hết hạn.",
    "err.waaw_hls_no_fallback": "Không tải được HLS stream và không có URL MP4 thay thế.",
    "err.waaw_hls_failed": "Không tải được HLS stream (ffmpeg: {tail}; fallback MP4: {err})",
    "err.waaw_captcha_timeout": (
        "Không giải captcha kịp thời gian.\n"
        "Thử lại và giải captcha trong cửa sổ trình duyệt vừa mở, hoặc dán link CDN mới lấy từ công cụ khác "
        "(vd: cf*cdn.com .m3u8)."
    ),
    "err.waaw_no_cdn_url": (
        "Không tìm thấy CDN URL sau {seconds} giây.\n"
        "waaw.ac có thể đã thay đổi cơ chế bảo vệ."
    ),
    "err.generic_error_word": "lỗi",
    "err.cancelled_by_user": "Đã hủy bởi người dùng.",
    "err.playwright_missing": "Thiếu thư viện Playwright.\nChạy: pip install playwright",
    "err.cdp_connect_failed": (
        "Không kết nối được CDP.\n"
        "Đóng trình duyệt hoàn toàn rồi thử lại.\n"
        "(chi tiết: {err})"
    ),
    "err.cdp_connect_failed_hard": (
        "Không kết nối được CDP.\n"
        "\n"
        "Đóng HOÀN TOÀN trình duyệt (kể cả System Tray) rồi thử lại.\n"
        "(chi tiết: {err})"
    ),
    "err.browser_not_found_cdp": (
        "Không tìm thấy {browser}.  Hãy cài đặt trình duyệt trước.\n"
        "Lưu ý: bản tải từ App Store không hỗ trợ CDP — cần bản từ website chính thức."
    ),
    "err.fb_not_story_url": "URL không phải Facebook Story.\nHãy dán URL dạng facebook.com/stories/...",
    "err.no_download_dir": "Thư mục tải về chưa được thiết lập",
    "err.fb_story_platform": "Facebook Story chỉ hỗ trợ Windows và macOS.\nLinux chưa được hỗ trợ.",
    "err.fb_story_browser_running": (
        "{browser} đang mở. Hãy đóng hẳn {browser} rồi bấm Tải lại.\n"
        "Lý do: trình duyệt chỉ chạy một bản cho mỗi hồ sơ, nên OmniDL không mở được "
        "cổng gỡ lỗi khi {browser} đã chạy."
    ),
    "err.fb_story_no_video_url": (
        "Không bắt được URL video của Story.\n"
        "\n"
        "Có thể do:\n"
        "• Story đã hết hạn (Stories tồn tại 24 giờ)\n"
        "• Bạn chưa đăng nhập Facebook trong Brave/Chrome\n"
        "• Story này chỉ có ảnh (không có video)\n"
        "\n"
        "Mở Story trong trình duyệt kiểm tra trước."
    ),
    "err.fb_story_incomplete_download": (
        "Bắt được URL video nhưng không tải được file hoàn chỉnh.\n"
        "\n"
        "Nguyên nhân thường gặp:\n"
        "• CDN URL đã hết hạn (load quá lâu)\n"
        "• Kết nối mạng không ổn định\n"
        "\n"
        "Hãy thử lại ngay sau khi mở Story trong trình duyệt."
    ),
    "progress.browser_start": "Đang khởi động trình duyệt...",
    "progress.browser_start_named": "Đang khởi động {browser}...",
    "progress.cdp_connect": "Đang kết nối CDP...",
    "progress.waaw_open_page": "Đang mở trang waaw.ac...",
    "progress.waaw_wait_cdn": "Đang chờ CDN URL...",
    "progress.waaw_captcha": "Trang yêu cầu captcha — hãy giải captcha trong cửa sổ trình duyệt vừa mở...",
    "progress.ffmpeg_hls": "ffmpeg đang tải HLS stream...",
    "progress.ffmpeg_dash": "ffmpeg đang xử lý DASH stream...",
    "progress.ffmpeg_dash_fetch": "FFmpeg đang tải video+audio từ DASH...",
    "progress.ffmpeg_merge": "FFmpeg đang ghép video + audio...",
    "progress.ks_browser_start": "Kuaishou: đang khởi động trình duyệt...",
    "progress.ks_cdp_connect": "Kuaishou: đang kết nối CDP...",
    "progress.ks_open_page": "Kuaishou: đang mở trang video...",
    "progress.fb_open_story": "Đang mở Story trong trình duyệt...",
    "progress.wait_video": "Đang chờ video load...",
    "progress.url_captured": "Đã bắt được URL — đang tải...",
    "progress.downloading_video": "Đang tải video...",
    "progress.downloading_kb": "Đang tải... {kb} KB",
    "progress.done_named": "✅ Hoàn thành! {name}",
    # ── Document convert (Markdown / HTML / Office <-> PDF) ───────────────
    "nav.docs": "Tài liệu",
    "docs.title": "Chuyển đổi tài liệu",
    "docs.subtitle": "Markdown ↔ PDF · HTML ↔ PDF · Office ↔ PDF",
    "docs.add_file": "Thêm tài liệu",
    "docs.remove_selected": "Xóa mục chọn",
    "docs.clear": "Xóa tất cả",
    "docs.source_label": "Tài liệu nguồn:",
    "docs.target_label": "Định dạng đích:",
    "docs.save_dir_label": "Thư mục lưu:",
    "docs.choose": "Chọn...",
    "docs.convert": "Chuyển đổi",
    "docs.cancel": "Hủy",
    "docs.page_n": "Trang {n}",
    "docs.file_filter": "Tài liệu ({exts});;Tất cả file (*)",
    "docs.no_file_title": "Chưa chọn tài liệu",
    "docs.no_file_msg": "Hãy thêm ít nhất một tài liệu để chuyển đổi.",
    "docs.no_target_title": "Không có định dạng đích",
    "docs.no_target_msg": (
        "Các tài liệu đang chọn không có định dạng đích chung. "
        "Hãy chuyển đổi chúng theo từng nhóm."
    ),
    "docs.status.converting": "Đang chuyển đổi {name}... ({i}/{total})",
    "docs.status.done": "Xong: {ok}/{total} tài liệu → {dir}",
    "docs.status.failed": "Chuyển đổi thất bại",
    "docs.status.cancelled": "Đã hủy chuyển đổi",
    "docs.done_title": "Hoàn thành",
    "docs.done_msg": "Đã chuyển đổi {ok}/{total} tài liệu.\nMở thư mục lưu?",
    "docs.error_title": "Lỗi chuyển đổi",
    "docs.caps_title": "Khả năng của máy này",
    "docs.cap.markdown": "Markdown → HTML (gói markdown)",
    "docs.cap.weasyprint": "HTML → PDF (WeasyPrint)",
    "docs.cap.pypdf": "PDF → văn bản (pypdf)",
    "docs.cap.libreoffice": "Office ↔ PDF (LibreOffice)",
    "docs.cap_ok": "Sẵn sàng",
    "docs.cap_missing": "Chưa cài",
    "docs.libreoffice_hint": (
        "Cài LibreOffice để bật chuyển đổi Office ↔ PDF (.docx, .xlsx, .pptx, .odt...)."
    ),
    "docs.recheck": "Kiểm tra lại",
    "docs.weasyprint_hint": (
        "Không tìm thấy thư viện vẽ PDF (Pango/GTK). Trên Windows hãy cài "
        "GTK for Windows Runtime để bật Markdown/HTML → PDF."
    ),
}

EN: dict[str, str] = {
    "nav.home": "Download",
    "nav.queue": "Queue",
    "nav.batch": "Batch",
    "nav.live_monitor": "Live",
    "nav.convert": "Convert",
    "nav.editor": "Editor",
    "nav.archive": "Archive",
    "nav.history": "History",
    "nav.settings": "Settings",
    "nav.special_dl": "Special",
    "nav.section.download": "DOWNLOAD",
    "nav.section.tools": "TOOLS",
    "nav.section.library": "LIBRARY",
    "nav.section.system": "SYSTEM",
    "topbar.theme_light": "☀ Light",
    "topbar.theme_dark": "🌙 Dark",
    "topbar.notifications": "Notifications",
    "topbar.hide_nav": "Hide navigation bar",
    "topbar.nav_strip": "Navigation bar",
    "topbar.toolbar_strip": "Analyse bar",
    "topbar.theme_changed": "Theme changed — restart the app to apply it everywhere.",
    "toolbar.url_placeholder": "Paste a video link here and press Enter or click Analyse...",
    "toolbar.analyse": "Analyse",
    "toolbar.analysing": "Analysing",
    "toolbar.analysing_dots": "Analysing...",
    "toolbar.stop": "Stop",
    "toolbar.paste_tip": "Paste from clipboard",
    "toolbar.clear_tip": "Clear URL",
    "toolbar.collapse_tip": "Hide analyse bar",
    "toolbar.status.clipboard": "URL from clipboard - press Enter to analyse",
    "toolbar.status.loading": "Loading media info...",
    "toolbar.status.cancelled": "Cancelled.",
    "toolbar.status.no_media": "No media found",
    "toolbar.status.pasted": "URL pasted",
    "toolbar.toast.no_result": "Analysis returned no result.",
    "toolbar.toast.ready": "Ready: {title}",
    "toolbar.toast.failed": "Analysis failed.",
    "toolbar.error.unknown": "Unknown error",
    "queue.title": "Download queue",
    "queue.select": "Select",
    "queue.select_tip": "Select multiple tasks",
    "queue.clear_finished": "Clear finished",
    "queue.clear_selected": "Clear selected ({count})",
    "queue.empty": "No tasks yet\nPaste a URL in the Download tab to start",
    "queue.count": "  {active} downloading  ·  {total} total  ",
    "queue.rename_title": "Rename file",
    "queue.rename_label": "New file name:",
    "queue.no_target": "No target device configured in Settings → Taildrop",
    "queue.taildrop_off": "Taildrop is not enabled in Settings",
    "queue.sent_to": "📲  Sent → {node}",
    "queue.send_failed": "❌  Send failed → {node}: {err}",
    "queue.sending": "📲  Sending to {count} device(s): {nodes}",
    "queue.send_error": "❌  File send error: {err}",
    "settings.search": "Search settings...",
    "settings.no_results": "No settings match your search.",
    "settings.section.location": "DOWNLOAD LOCATION",
    "settings.section.behaviour": "DOWNLOAD BEHAVIOUR",
    "settings.section.appearance": "APPEARANCE",
    "settings.section.clipboard": "CLIPBOARD MONITOR",
    "settings.section.developer": "DEVELOPER",
    "settings.browse": "Browse...",
    "settings.max_concurrent": "Max concurrent downloads",
    "settings.restart_hint": "⚠  Takes effect after restarting the app.",
    "settings.max_retries": "Retry attempts on error",
    "settings.embed_thumbnail": "Embed thumbnail",
    "settings.embed_metadata": "Embed metadata",
    "settings.theme": "Colour theme",
    "settings.language": "Language",
    "settings.language_hint": (
        "Applies to the main interface immediately; other screens follow after a restart."
    ),
    "settings.language_changed": "Language changed to {language}",
    "settings.clipboard_watch": "Auto-detect URLs from the clipboard",
    "settings.clipboard_hint": "Checks the clipboard every 1.5 s and fills the URL bar automatically",
    "settings.debug_logging": "Verbose logging (debug)",
    "settings.debug_hint": "Writes details to omnidl_debug.log  •  No restart needed",
    "settings.folder_not_found": "Folder not found. Please pick it again.",
    "settings.folder_create_failed": "Could not create folder: {err}",
    "settings.select_folder": "Select download folder",
    "history.title": "Download history",
    "history.clear_all": "Clear all",
    "history.search_placeholder": "Search by title, URL or filename...",
    "history.filter.all": "All",
    "history.status.completed": "Completed",
    "history.status.failed": "Failed",
    "history.status.cancelled": "Cancelled",
    "history.load_more": "Load more ({remaining} left)",
    "history.empty_no_results": "No results found",
    "history.empty_no_history": "No download history yet",
    "history.redownload": "Re-download",
    "history.redownload_tip": "Download again",
    "history.copy_url": "Copy URL",
    "history.copy_url_tip": "Copy URL",
    "history.folder": "Folder",
    "history.folder_tip": "Open containing folder",
    "history.rename": "Rename",
    "history.rename_tip": "Rename file",
    "history.delete": "Delete",
    "history.delete_tip": "Remove from history",
    "history.clear_confirm": "Clear all download history?",
    "special.title": "Special downloads",
    "special.subtitle": "Download content yt-dlp does not support — Facebook Story, etc.",
    "special.platform_label": "Platform:",
    "special.browser_label": "Browser:",
    "special.url_placeholder.facebook_story": "https://www.facebook.com/stories/...",
    "special.guide.facebook_story": (
        "Guide:\n"
        "1. Fully close Brave / Chrome (including System Tray / Dock)\n"
        "2. Paste the Story URL in the box above\n"
        "3. Pick a browser → click Download\n"
        "4. The browser opens itself; the download finishes automatically\n"
        "Stories expire after 24 hours\n"
        "Use the build installed from the website — the App Store version is not supported"
    ),
    "special.download_btn": "Download",
    "special.downloading": "Downloading...",
    "special.status.waiting": "Waiting...",
    "special.status.starting": "Starting...",
    "special.retry": "Retry",
    "special.clear_history": "Clear history",
    "special.open_folder": "Open folder",
    "special.view": "View",
    "special.error.paste_url": "Paste a URL in the box above.",
    "special.error.not_story_url": (
        "This is not a Facebook Story link.\n"
        "A Story looks like facebook.com/stories/... — use the Download tab for "
        "fb.watch links and ordinary videos."
    ),
    "special.downloaded": "Downloaded: {name}",
    "special.no_result": "No result.",
    "special.error_prefix": "Error: {msg}",
    "special.convert_unavailable": "Convert service is unavailable.",
    "special.converting": "Converting → .{ext}…",
    "special.convert_done": "Convert done: {name}",
    "special.sent_to": "Sent → {node}",
    "special.send_failed": "Send failed → {node}: {err}",
    "special.sending": "Sending to: {nodes}",
    "special.send_error": "Send error: {err}",
    "special.file_deleted": "File deleted.",
    "archive.title": "Compress / Extract",
    "archive.mode.compress": "Compress",
    "archive.mode.extract": "Extract",
    "archive.cancel": "Cancel",
    "archive.add_file": "Add file",
    "archive.add_folder": "Add folder",
    "archive.remove_selected": "Remove selected",
    "archive.format_label": "Format:",
    "archive.compress_individually": "Compress each file separately",
    "archive.encrypt_names": "Encrypt filenames (7z only)",
    "archive.password_label": "Password:",
    "archive.password_placeholder": "Leave empty for no password",
    "archive.name_label": "Archive name:",
    "archive.use_orig_name": "Use original name",
    "archive.use_orig_name_tip": (
        'Only available when exactly one file/folder is selected and "Compress each file separately" is off'
    ),
    "archive.save_dir_label": "Save folder:",
    "archive.choose": "Choose...",
    "archive.file_label": "Archive file:",
    "archive.extract_dest_label": "Extract to:",
    "archive.view_contents": "View contents",
    "archive.no_file_title": "No file selected",
    "archive.no_file_compress_msg": "Add at least one file or folder to compress.",
    "archive.no_file_extract_msg": "Choose an archive file to extract.",
    "archive.no_file_list_msg": "Choose an archive file.",
    "archive.status.compressing": "Compressing...",
    "archive.status.extracting": "Extracting...",
    "archive.status.listing": "Reading contents...",
    "archive.status.compress_done": "Compressed {count} archive(s) into {dir}",
    "archive.status.compress_cancelled": "Compression cancelled",
    "archive.status.compress_failed": "Compression failed",
    "archive.status.extract_done": "Extracted {count} file(s) ({size}) into {dir}",
    "archive.status.extract_cancelled": "Extraction cancelled",
    "archive.status.extract_failed": "Extraction failed",
    "archive.status.list_count": "{count} item(s) in the archive",
    "archive.status.list_failed": "Could not read contents",
    "archive.done_title": "Done",
    "archive.compress_done_msg": "Created {count} archive(s).\nOpen the containing folder?",
    "archive.extract_done_msg": "Extraction complete.\nOpen the folder?",
    "archive.error_compress_title": "Compression error",
    "archive.error_extract_title": "Extraction error",
    "archive.error_title": "Error",
    "archive.choose_file": "Choose file",
    "archive.choose_folder": "Choose folder",
    "archive.choose_save_folder": "Choose save folder",
    "archive.choose_archive_file": "Choose archive file",
    "archive.choose_extract_folder": "Choose extraction folder",
    "batch.title": "Batch download",
    "batch.hint": "Paste URLs here — one per line (up to {max})",
    "batch.import_txt": "Import .txt",
    "batch.clear_all": "Clear all",
    "batch.results_title": "Analysis results",
    "batch.select_all": "Select all",
    "batch.sequential": "Download sequentially",
    "batch.quality_label": "Quality:",
    "batch.retry_errors": "Retry errors",
    "batch.retry_count": "Retry {count} error(s)",
    "batch.queue_all": "Add all",
    "batch.queue_count": "Queue {count} video(s)",
    "batch.queue_add_count": "Add {count} video(s)",
    "batch.empty": "No URLs yet — paste links above or import a .txt file",
    "batch.analysing_dots": "Analysing…",
    "batch.analyse_again": "Analyse again",
    "batch.retrying_dots": "Retrying…",
    "batch.no_valid_urls": "No valid URLs ({errors} error(s))",
    "batch.ready_summary": "✓  {ready}/{total} ready{err_note}",
    "batch.ready_err_note": "  ·  {errors} error(s)",
    "batch.retrying_urls": "Retrying {count} failed URL(s)…",
    "batch.analysis_failed": "Analysis failed",
    "batch.import_dialog_title": "Import URL list",
    "batch.import_read_error": "Could not read file: {err}",
    "batch.import_no_valid": "No valid URLs found in the file.",
    "batch.import_done": "Imported {count} URL(s)",
    "batch.import_capped": "(capped at {max}, skipped {skipped})",
    "batch.import_invalid": "· {count} invalid line(s) skipped",
    "batch.url_count": "{count} URL(s)",
    "batch.url_count_capped": "{count} URLs — only the first {max} are used",
    "batch.queued_toast": "Added {count} video(s) to the queue.",
    "batch.queue_added": "✓  Added to queue",
    "batch.queue_added_summary": "✓  {count} video(s) added to the queue",
    "batch.downloading_sequential": "⬇  Downloading sequentially…",
    "batch.sequential_status": "Sequential download: {count} video(s)",
    "batch.sequential_toast": "Downloading sequentially — {remaining} video(s) left.",
    "batch.sequential_done_btn": "✓  Download complete",
    "batch.sequential_done_status": "✓  Sequential download complete",
    "batch.playlist_label_default": "playlist",
    "batch.playlist_loaded": "Loaded {count} video(s) from {label} into Batch.",
    "batch.queue_all_failed": "Could not queue any video ({count} error(s))",
    "batch.cancel_analyse": "Stop analysing",
    "batch.analyse_stopped": "Analysis stopped",
    "batch.analysing_progress": "Analysing {done}/{total}…",
    "editor.empty": "No video loaded. Open a file or click 'Edit' from the Queue tab.",
    "editor.hide_panel": "⊡ Hide panel",
    "editor.show_panel": "⊞ Show panel",
    "editor.open_file": "📂  Open file",
    "editor.close_file": "✕  Close file",
    "editor.close_file_tip": "Close the current file",
    "editor.set_in": "[ Set In",
    "editor.set_out": "Set Out ]",
    "editor.in_label": "In: {time}",
    "editor.out_label": "Out: {time}",
    "editor.out_placeholder": "Out: --:--",
    "editor.rotate_label": "Rotate:",
    "editor.rotate.none": "No rotation",
    "editor.rotate.cw90": "90° clockwise",
    "editor.rotate.ccw90": "90° counter-clockwise",
    "editor.rotate.180": "180°",
    "editor.mute": "Mute",
    "editor.preview_btn": "▶ Preview",
    "editor.export_btn": "✂  Export",
    "editor.speed_label": "Speed:",
    "editor.volume_label": "Volume:",
    "editor.text_label": "Text:",
    "editor.text_placeholder": "Text overlay on the video...",
    "editor.position_label": "Position:",
    "editor.pos.top": "Top",
    "editor.pos.middle": "Middle",
    "editor.pos.bottom": "Bottom",
    "editor.size_label": "Size:",
    "editor.color_label": "Color:",
    "editor.color.white": "White",
    "editor.color.black": "Black",
    "editor.color.yellow": "Yellow",
    "editor.color.red": "Red",
    "editor.box": "Background",
    "editor.shadow": "Shadow",
    "editor.effects_title": "Video effects",
    "editor.hide": "- Hide",
    "editor.show": "+ Show",
    "editor.brightness": "Brightness:",
    "editor.contrast": "Contrast:",
    "editor.saturation": "Saturation:",
    "editor.hue": "Hue:",
    "editor.blur": "Blur:",
    "editor.fade_in": "Fade in:",
    "editor.fade_out": "Fade out:",
    "editor.creating_preview_btn": "⏳ Creating...",
    "editor.creating_preview_status": "Creating preview...",
    "editor.back_to_original": "← Original",
    "editor.previewing_status": "Previewing (10s)",
    "editor.preview_error": "Preview error: {msg}",
    "editor.export_cancel_btn": "Cancel",
    "editor.exporting_status": "Exporting...",
    "editor.in_after_out_error": "In must be before Out",
    "editor.export_saved": "Saved: {name}",
    "editor.export_error": "Error: {msg}",
    "editor.choose_video_file": "Choose video file",
    "editor.all_files_filter": "All",
    "live.title": "Live monitor",
    "live.hint": "Paste a live stream URL — Instagram, YouTube, TikTok, Facebook, Twitch…  (up to {max})",
    "live.add_btn": "Add",
    "live.check_interval_label": "Check every:",
    "live.seconds_label": "seconds",
    "live.pause": "Pause",
    "live.resume": "Resume",
    "live.empty": "No URLs being watched yet\nPaste a live stream URL above to start auto-recording",
    "live.state.waiting": "Waiting for live",
    "live.state.checking": "Checking…",
    "live.state.live": "LIVE now",
    "live.state.recording": "Recording",
    "live.state.ended": "Recording finished",
    "live.check_now": "Check",
    "live.open": "Open",
    "live.convert_btn": "Convert",
    "live.invalid_url": "Invalid URL — must start with http:// or https://",
    "live.limit_reached": "Reached the {max} URL limit.",
    "live.already_watching": "This URL is already being watched.",
    "live.ig_cookie_needed": (
        "Profile watching needs an Instagram cookie file.\nConfigure it in Settings → Network → Cookie file."
    ),
    "live.watching_toast": "Watching {label} — recording starts automatically once live.",
    "live.profile_watch_suffix": "(profile watch)",
    "live.paused_status": "Paused",
    "live.rate_limited": "Rate limited — retry in {wait}s",
    "live.fail_hint": "  ({failed}/{max} failed)",
    "live.recheck_in": "Rechecking in {remaining}s{fail_hint}",
    "live.new_check_in": "Next check in {interval}s",
    "live.checking_stream": "Checking stream…",
    "live.stream_live_starting": "Stream is live — starting recording…",
    "live.recording_dots": "Recording…",
    "live.cookie_old": "Cookie is {age} days old — auth may fail",
    "live.cookie_banner": (
        "Cookie file is {age} days old — Instagram/TikTok Live may fail. "
        "Refresh the cookie in Settings → Network."
    ),
    "live.check_stuck_error": "Check stuck for {elapsed}s. Retry or check your network connection.",
    "live.download_failed": "Download failed",
    "live.file_not_found": ".ts file not found.",
    "live.retry_failure_error": (
        "Failed {failures} times. Last error: {err}\n"
        "Check your Instagram cookie or network connection, then click x and add the URL again."
    ),
    "live.status_recording": "{count} recording",
    "live.status_waiting": "{count} waiting",
    "live.status_ended": "{count} done",
    "convert.quality.high.label": "High quality",
    "convert.quality.high.desc": "H.264 CRF 18 · AAC 192k · Keeps resolution",
    "convert.quality.standard.label": "Standard",
    "convert.quality.standard.desc": "H.264 CRF 23 · AAC 128k · Works on every iPhone",
    "convert.quality.small.label": "Small file",
    "convert.quality.small.desc": "H.264 CRF 28 · AAC 96k · Up to 720p",
    "convert.quality.custom.label": "Custom",
    "convert.quality.custom.desc": "Custom CRF/CQ value (16-35)",
    "convert.state.pending": "Pending",
    "convert.state.queued": "Queued",
    "convert.state.converting": "Converting…",
    "convert.state.done": "✓ Done",
    "convert.state.failed": "✕ Error",
    "convert.card.delete_output": "Delete file",
    "convert.card.output_gone": "→ {name} (removed from disk)",
    "convert.card.before": "Before",
    "convert.card.after": "After",
    "convert.card.reading": "Reading…",
    "convert.card.reading_info": "Reading info…",
    "convert.clear_done": "Clear done/failed",
    "convert.select_all": "Select all",
    "convert.select_all_tip": "Select / deselect every pending file.",
    "convert.select_file_tip": "Include this file in the conversion.",
    "convert.parallel_label": "Parallel:",
    "convert.parallel_tip": (
        "How many files convert at the same time (1-8). "
        "Higher is faster but uses more CPU."
    ),
    "convert.settings_toggle_up": "⚙ Settings  ▲",
    "convert.settings_toggle_down": "⚙ Settings  ▼",
    "convert.quality_label": "Quality",
    "convert.custom_value_label": "Custom value (16-35):",
    "convert.encoder_label": "Encoder",
    "convert.speed_label": "Speed",
    "convert.codec_label": "Codec",
    "convert.subtitle_label": "Subtitles",
    "convert.gen_subs_check": "Auto-generate subtitles (.srt)",
    "convert.rating_label": "Rating",
    "convert.vmaf_check": "Score VMAF against the original file",
    "convert.vmaf_tip": (
        "Compares the converted video against the original using VMAF (0-100).\n"
        "A higher score means it's closer to the original. Scoring re-decodes both files, "
        "so it adds extra time."
    ),
    "convert.empty_title": "No files yet",
    "convert.empty_hint": 'Click "Add file" or "Add folder" to pick videos',
    "convert.empty_formats": "Supported: MP4, MKV, WebM, AVI, MOV, FLV, WMV, TS, 3GP…",
    "convert.generate_subs_btn": "Generate subtitles",
    "convert.generate_subs_tip": (
        "Only creates an .srt from the video's speech — does not re-encode the video."
    ),
    "convert.convert_all_btn": "Convert all",
    "convert.no_whisper_tip": (
        "The current FFmpeg build has no whisper filter.\n"
        "A 'full' FFmpeg build is needed (see README) to auto-generate subtitles."
    ),
    "convert.first_enable_tip": (
        "Enabling it the first time downloads the speech-recognition model (~141 MB), only once."
    ),
    "convert.gpu_status": "GPU: {labels}",
    "convert.cpu_only_status": "CPU only",
    "convert.choose_videos_title": "Choose videos to convert",
    "convert.choose_folder_title": "Choose a folder with videos",
    "convert.scanning_folder": "Scanning folder…",
    "convert.no_video_in_folder": "No videos found in {folder}",
    "convert.added_from_folder": "Added {count} file(s) from {folder}",
    "convert.subtitle_created": "Subtitle created: {name}",
    "convert.status.processing": "Processing {count}",
    "convert.status.waiting_count": "{count} waiting",
    "convert.status.total_files": " / {total} file(s)",
    "convert.status.complete": "Done {done}/{total} file(s) ✓",
    "convert.status.done_count": "{count} done",
    "convert.status.failed_count": "{count} failed",
    "convert.delete_output_title": "Delete converted file",
    "convert.delete_output_msg": (
        "Are you sure you want to delete the converted file on this computer?\n\n"
        "{name}\n\n"
        "Make sure the file has been saved to your iPhone before deleting.\n"
        "This action cannot be undone."
    ),
    "convert.delete_error_title": "Delete error",
    "convert.delete_error_msg": "Could not delete the file:\n{err}",
    "convert.taildrop_sent": "Sent '{name}' to {node}",
    "convert.taildrop_failed": "Taildrop failed '{name}': {err}",
    "settings.taildrop.description": (
        "After a download finishes, automatically send the file to your iPhone via Taildrop (Tailscale).\n"
        "Requires: Tailscale CLI on this PC and Taildrop enabled on the iPhone.\n"
        "The file shows up in iOS's Files app."
    ),
    "settings.taildrop.enable": "Enable Taildrop",
    "settings.taildrop.cli_found": "✅  tailscale CLI found on PATH",
    "settings.taildrop.cli_not_found": "⚠️  tailscale CLI not found — install Tailscale on this PC",
    "settings.taildrop.mode_header": "📤  Send mode",
    "settings.taildrop.mode_auto_btn": "🔄  Automatic",
    "settings.taildrop.mode_manual_btn": "🖱️  Manual",
    "settings.taildrop.mode_auto_desc": (
        "⚡ The file is sent to your iPhone automatically right after each download."
    ),
    "settings.taildrop.mode_manual_desc": (
        "🖱️ The file is only sent when you click Transfer manually in the Remote UI."
    ),
    "settings.taildrop.nodes_header": "📱  Target device(s) (pick one or more)",
    "settings.taildrop.nodes_hint": "Click 🔍 Find devices to scan. Check the machines to send to.",
    "settings.taildrop.no_nodes": "No devices yet. Click 🔍 to scan.",
    "settings.taildrop.manual_label": "Or type a node name manually:",
    "settings.taildrop.manual_placeholder": "e.g. iphone or 100.64.x.x",
    "settings.taildrop.add_btn": "➕ Add",
    "settings.taildrop.scan_btn": "🔍  Find Tailscale devices",
    "settings.taildrop.toggle_on": "📲  Taildrop enabled.",
    "settings.taildrop.toggle_off": "📲  Taildrop disabled.",
    "settings.taildrop.mode_auto_label": "Automatic — sends right after each download finishes",
    "settings.taildrop.mode_manual_label": "Manual — only sends when you ask",
    "settings.taildrop.mode_toast": "📤  Taildrop mode: {label}.",
    "settings.taildrop.invalid_node": ("❌  Invalid node name — letters, digits, hyphens, and dots only."),
    "settings.taildrop.node_added": "➕  Added node: {node}",
    "settings.taildrop.scanning": "⏳  Scanning...",
    "settings.taildrop.no_devices_found": (
        "⚠️  No other devices found online.\n"
        "→ Open the Tailscale app on your iPhone and make sure it's connected."
    ),
    "settings.taildrop.devices_found": "✅  Found {count} device(s). Check the ones to send to.",
    "settings.tools.section.ytdlp": "YT-DLP ENGINE",
    "settings.tools.section.gallery_dl": "GALLERY-DL ENGINE",
    "settings.tools.section.data_privacy": "DATA & PRIVACY",
    "settings.tools.version_label": "Version",
    "settings.tools.update_ytdlp_btn": "Update yt-dlp",
    "settings.tools.checking_updates": "Checking for updates…",
    "settings.tools.updating_toast": "Updating {tool}, please wait…",
    "settings.tools.updated_status": "Updated → {ver}",
    "settings.tools.updated_toast": "{tool} updated to {ver}",
    "settings.tools.update_error_status": "Error: {msg}",
    "settings.tools.update_failed_toast": "Update failed: {msg}",
    "settings.tools.keyring_btn": "Install keyring (supports Brave/Chrome 127+)",
    "settings.tools.keyring_not_installed": "⚠ Not installed — needed for Brave/Chrome 127+",
    "settings.tools.keyring_warning": (
        "⚠  Needed if Brave/Chrome reports a DPAPI error when fetching cookies."
    ),
    "settings.tools.extra_args_label": "Extra arguments",
    "settings.tools.extra_args_placeholder": "e.g. --no-playlist",
    "settings.tools.gallery_update_btn": "Update gallery-dl",
    "settings.tools.clear_data_desc": (
        "Clears all app data: download history, configuration settings,\n"
        "and the cookie file path. The cookie file on disk is not deleted.\n"
        "Cannot be done while a download/conversion is running."
    ),
    "settings.tools.clear_data_btn": "🗑  Clear all data",
    "settings.tools.keyring_bundled_suffix": " (bundled)",
    "settings.tools.keyring_bundled_toast": (
        "keyring is already bundled in this build. Try fetching cookies again."
    ),
    "settings.tools.keyring_missing_status": "keyring not found — download a newer build",
    "settings.tools.keyring_missing_toast": (
        "keyring not found in this build. Please download the latest EXE."
    ),
    "settings.tools.installing_keyring": "Installing keyring…",
    "settings.tools.keyring_installed_toast": (
        "keyring installed. Try fetching cookies from Brave/Chrome again."
    ),
    "settings.tools.keyring_install_failed_toast": "Failed to install keyring: {msg}",
    "settings.tools.clear_data_running": "Cannot clear data while {parts}.",
    "settings.tools.active_downloads": "{count} download(s) running",
    "settings.tools.active_conversions": "{count} conversion(s) running",
    "settings.tools.and_join": " and ",
    "settings.tools.confirm_clear_title": "OmniDL — Confirm data deletion",
    "settings.tools.confirm_clear_msg": (
        "This will:\n"
        "  • Clear all download history\n"
        "  • Reset all settings to defaults\n"
        "  • Remove the cookie file path from the configuration\n\n"
        "The cookie file and downloaded files will not be affected.\n\nContinue?"
    ),
    "settings.tools.data_cleared_status": "✓ Cleared",
    "settings.tools.data_cleared_toast": "All data cleared. Restart the app to fully apply it.",
    "settings.tools.clear_failed_toast": "Failed to clear data: {msg}",
    "settings.api.section.remote": "REMOTE API",
    "settings.api.section.tailscale": "TAILSCALE HTTPS PROFILE",
    "settings.api.description": (
        "Enable to control OmniDL remotely over the LAN (iPhone, Android).\n"
        "The server runs on its own thread — it does not affect current downloads.\n"
        "Only enable when needed — disable it when not in use for device security."
    ),
    "settings.api.enable": "Enable Remote API",
    "settings.api.token_header": "🔑  Bearer Token",
    "settings.api.copy_btn": "📋 Copy",
    "settings.api.rotate_btn": "🔄  Generate new token",
    "settings.api.ts_description": (
        "Access the Remote API over HTTPS on your Tailscale network.\n"
        "OmniDL runs tailscale serve automatically — no firewall port to open.\n"
        "Requires: Tailscale installed and logged in on this machine."
    ),
    "settings.api.ts_enable": "Enable Tailscale HTTPS Profile",
    "settings.api.reset_profile_btn": "Reset Profile",
    "settings.api.no_token": "(no token yet — enable the API to generate one automatically)",
    "settings.api.running": "🟢  Running  —  http://<LAN IP>:{port}",
    "settings.api.enabled_not_started": "⚠️  Enabled but not started (missing fastapi/uvicorn?)",
    "settings.api.disabled": "⚫  Disabled",
    "settings.api.enabled_toast": "✅  Remote API enabled.",
    "settings.api.missing_deps": (
        "⚠️  Need to install fastapi & uvicorn first: pip install -r requirements-api.txt"
    ),
    "settings.api.start_error": "Error starting API: {err}",
    "settings.api.disabled_toast": "⚫  Remote API disabled.",
    "settings.api.no_token_toast": "No token yet. Enable Remote API first.",
    "settings.api.token_copied": "✅  Token copied to clipboard.",
    "settings.api.rotate_dialog_title": "Generate new token",
    "settings.api.rotate_confirm_msg": (
        "The current token will be invalidated.\n"
        "All connected devices need to update to the new token.\n\nContinue?"
    ),
    "settings.api.new_token_saved": "✅  New token saved",
    "settings.api.restarting_toast": "🔄  New token generated — restarting the server…",
    "settings.api.restarted_toast": "✅  Server restarted with the new token.",
    "settings.api.restart_error": "Error restarting API: {err}",
    "settings.api.new_token_toast": "✅  New token generated. Copy it and update it on your devices.",
    "settings.api.ts_running": "Remote API is running at:\nhttps://{dns}",
    "settings.api.ts_internal_port": "Internal port (random): {port}",
    "settings.api.ts_setting_up": "Setting up... (check whether Tailscale is connected)",
    "settings.api.ts_internal_port_plain": "Internal port: {port}",
    "settings.api.ts_off": "Disabled",
    "settings.api.enable_api_first": "Enable Remote API before using Tailscale HTTPS Profile.",
    "settings.api.no_tailscale_cli": "tailscale CLI not found — install Tailscale on this machine.",
    "settings.api.setting_up_https": "Setting up Tailscale HTTPS Profile...",
    "settings.api.login_hint": " Tailscale not logged in? Run 'tailscale login' and try again.",
    "settings.api.serve_failed": "tailscale serve failed.{hint}",
    "settings.api.https_enabled_toast": "HTTPS Profile enabled: https://{dns}",
    "settings.api.no_dns_error": (
        "serve started but could not get a DNS name. Check that Tailscale is logged in."
    ),
    "settings.api.setup_error": "Error setting up HTTPS Profile: {err}",
    "settings.api.disabling_https": "Disabling Tailscale HTTPS Profile...",
    "settings.api.https_disabled_toast": "Tailscale HTTPS Profile disabled.",
    "settings.api.disable_error": "Error disabling HTTPS Profile: {err}",
    "settings.api.enable_https_first": "Enable HTTPS Profile before resetting it.",
    "settings.api.reset_confirm_msg": (
        "This will:\n• Recreate the Tailscale serve profile\n"
        "• Generate a new token (devices need to update)\n\nContinue?"
    ),
    "settings.api.resetting_status": "Resetting...",
    "settings.api.resetting_toast": "Resetting Tailscale HTTPS Profile...",
    "settings.api.reset_serve_failed": (
        "tailscale serve failed during reset. Check that Tailscale is logged in."
    ),
    "settings.api.reset_success_dns": (
        "Profile reset: https://{dns}. New token generated — update your devices."
    ),
    "settings.api.reset_success": "Profile reset. New token generated — update your devices.",
    "settings.api.reset_error": "Error resetting Profile: {err}",
    "settings.network.section.auth": "NETWORK & AUTHENTICATION",
    "settings.network.proxy_label": "Proxy URL",
    "settings.network.browser_label": "Source browser",
    "settings.network.use_cookies_label": "Use cookies",
    "settings.network.section.auto_extract": "AUTO-EXTRACT COOKIES",
    "settings.network.extract_global_btn": "🔄  Firefox / Edge / Opera",
    "settings.network.extract_cdp_btn": "🦁  Brave / Chrome 127+",
    "settings.network.extract_hint": (
        "🔄 = yt-dlp reads directly (close Brave/Chrome first)   •   "
        "🦁 = CDP — no need to close the browser, safe on Brave 127+"
    ),
    "settings.network.section.manual_import": "MANUAL FILE IMPORT",
    "settings.network.fallback_hint": (
        "🌐  Cookie fallback — YouTube, Twitch, Vimeo...  "
        "(used when a platform has no row of its own in the Per-Platform table below)"
    ),
    "settings.network.fallback_warning": (
        "⚠  This file contains all of the browser's cookies (Google, email, banking...).\n"
        "   Prefer the Per-Platform table below for better security."
    ),
    "settings.network.browse_btn": "Browse…",
    "settings.network.clear_btn": "🗑 Clear",
    "settings.network.section.per_platform": "PER-PLATFORM COOKIES",
    "settings.network.recommended_badge": "RECOMMENDED",
    "settings.network.per_platform_desc": (
        "✅ Prefer this table — each file only contains cookies for that one platform.\n"
        "The TikTok file has no Google/email cookies, the Instagram file has no banking cookies.\n"
        "If a platform has its own row here → you do NOT need the Cookie fallback above."
    ),
    "settings.network.cdp_btn": "CDP",
    "settings.network.cdp_tip": "CDP — Brave/Chrome 127+ (no need to close the browser)",
    "settings.network.ytdlp_btn": "yt-dlp",
    "settings.network.ytdlp_tip": "yt-dlp — Firefox / Edge / Opera (close Brave/Chrome first)",
    "settings.network.choose_btn": "Choose",
    "settings.network.choose_tip": "Manually choose a cookie file (.txt Netscape)",
    "settings.network.delete_tip": "Delete this platform's cookie file",
    "settings.network.extract_footer_hint": (
        "🔄 = yt-dlp (Firefox/Opera).  🦁 = CDP (Brave/Chrome 127+, no need to close the browser)."
    ),
    "settings.network.section.tiktok_accounts": "TIKTOK ACCOUNTS",
    "settings.network.pool_badge": "POOL",
    "settings.network.tiktok_desc": (
        "Each account gets up to N simultaneous download slots.\n"
        "When the pool is empty, the app uses the 'Per-Platform TikTok cookie' above."
    ),
    "settings.network.no_accounts": "No accounts yet. Click '+ Add account' to add one.",
    "settings.network.add_account_btn": "+ Add account",
    "settings.network.name_label": "Name:",
    "settings.network.name_placeholder": "Account 1",
    "settings.network.no_cookie_chosen": "No cookie chosen",
    "settings.network.save_btn": "Save",
    "settings.network.slots_tip": "Max simultaneous downloads for this account",
    "settings.network.resume_btn": "Resume",
    "settings.network.pause_btn": "Pause",
    "settings.network.default_account_name": "Account",
    "settings.network.select_tiktok_cookie_title": "Choose TikTok cookie file (Netscape format)",
    "settings.network.not_netscape_format": "File is not in Netscape cookie format.",
    "settings.network.copy_failed": "Could not copy file: {err}",
    "settings.network.cdp_unsupported_browser": "CDP only supports Brave/Chrome/Edge.",
    "settings.network.starting_browser": "Starting {browser}...",
    "settings.network.cdp_failed": "CDP failed: {err}",
    "settings.network.cdp_got_tiktok": "CDP: got {count} TikTok cookies.",
    "settings.network.reading_tiktok_from": "Reading TikTok cookies from {browser}...",
    "settings.network.extract_failed": "Failed: {err}",
    "settings.network.got_tiktok_cookies": "Got {count} TikTok cookies.",
    "settings.network.account_added": "Added account '{name}'.",
    "settings.network.profile_label": "Profile:",
    "settings.network.profile_default": "Default",
    "settings.network.profile_tip": (
        "Each browser profile keeps its own login session.\n"
        "Sign each TikTok account into a different profile, then add each profile here."
    ),
    "settings.network.slots_label": "Slots:",
    "settings.network.source_manual": "Manual file",
    "settings.network.cookie_ready": "Ready — {count} cookies, signed in.",
    "settings.network.reject_missing": "The extracted cookie file is missing.",
    "settings.network.reject_unreadable": "Could not read the cookie file.",
    "settings.network.reject_not_logged_in": (
        "This profile is not signed in to TikTok. Sign in on that profile, then extract again."
    ),
    "settings.network.reject_expired": (
        "The login session has expired. Sign in again in the browser, then extract again."
    ),
    "settings.network.duplicate_account": (
        "This exact TikTok account is already in the pool ('{name}'). Pick a different browser profile."
    ),
    "settings.network.name_in_use": "The name '{name}' is already used.",
    "settings.network.rename_tip": "Click to rename this account",
    "settings.network.refresh_btn": "↻",
    "settings.network.refresh_tip": "Re-extract cookies for this account (same browser/profile)",
    "settings.network.refresh_no_source": (
        "This account was added from a manual file — delete it and add a fresh file."
    ),
    "settings.network.refreshing": "Refreshing cookies for '{name}'...",
    "settings.network.account_refreshed": "Refreshed cookies for '{name}'.",
    "settings.network.status_ok": "Active — about {days} days left",
    "settings.network.status_ok_session": "Active",
    "settings.network.status_paused": "Paused",
    "settings.network.status_missing": "Cookie file missing",
    "settings.network.status_unreadable": "Cookie file unreadable",
    "settings.network.status_not_logged_in": "Cookie is not signed in to TikTok",
    "settings.network.status_expired": "Cookie expired — click ↻ to re-extract",
    "settings.network.pool_hint": (
        "Tip: one TikTok account = one browser profile. "
        "Create a new profile in Brave/Chrome/Edge, sign the second account in there, "
        "then come back and pick that profile — no need to sign anyone out."
    ),
    "settings.network.account_rejected": (
        "Could not add the account — the cookie file must live inside the app's cookies folder."
    ),
    "settings.network.proxy_invalid": (
        "Invalid proxy — must start with http://, https://, socks4://, or socks5://"
    ),
    "settings.network.select_cookies_title": "Select cookies.txt (Netscape format)",
    "settings.network.file_not_found": "File not found.",
    "settings.network.not_netscape_full": (
        "File is not in Netscape cookie format.\n"
        "Choose a cookies.txt exported from your browser or the Cookie-Editor extension."
    ),
    "settings.network.copy_failed_full": "Cannot copy cookie file: {err}",
    "settings.network.cookie_saved_encrypted": "Cookie file encrypted and saved to a secure folder.",
    "settings.network.no_file_selected": "No file selected",
    "settings.network.cdp_confirm_title": "OmniDL — Confirm fetching all cookies (CDP)",
    "settings.network.cdp_confirm_msg": (
        "⚠ This fetches ALL of Brave/Chrome's cookies,\n"
        "including Google, email, banking...\n\n"
        "Cookies are encrypted with DPAPI and stored only on this machine.\n"
        "A random port on localhost will be opened for ~10 seconds.\n\n"
        "➡ Recommended: use the 🦁 button per platform below\n"
        "   to fetch only the cookies you actually need (safer).\n\n"
        "Continue fetching everything?"
    ),
    "settings.network.cdp_browser_unsupported": (
        "CDP only supports Brave/Chrome/Edge. Current browser: {browser}.\n"
        "Use the 🔄 button for Firefox/Opera/Safari."
    ),
    "settings.network.error_status": "❌ {err}",
    "settings.network.cdp_global_failed_toast": "CDP failed: {err}",
    "settings.network.cdp_saved_status": "✓ {count} cookie(s) saved (CDP)",
    "settings.network.cdp_from_browser_toast": "CDP: got {count} cookies from {browser}.",
    "settings.network.starting_cdp_status": "Starting {browser} (CDP)…",
    "settings.network.global_confirm_title": "OmniDL — Confirm fetching all cookies",
    "settings.network.global_confirm_msg": (
        "⚠ This fetches ALL of the browser's cookies,\n"
        "including Google, email, banking...\n\n"
        "Cookies are encrypted with DPAPI and stored only on this machine.\n\n"
        "➡ Recommended: use the 🔄 / 🦁 buttons per platform below\n"
        "   to fetch only the cookies you actually need (safer).\n\n"
        "Continue fetching everything?"
    ),
    "settings.network.extract_failed_toast": "Failed to fetch cookies: {err}",
    "settings.network.saved_status": "✓ {count} cookie(s) saved{note}",
    "settings.network.encrypted_note": " 🔒 (DPAPI encrypted)",
    "settings.network.got_from_browser_toast": "Got {count} cookies from {browser}{note}.",
    "settings.network.reading_from_browser_status": "Reading cookies from {browser}…",
    "settings.network.select_cookie_for": "Choose cookie file for {platform} (Netscape format)",
    "settings.network.file_not_found_vi": "File not found.",
    "settings.network.copy_platform_failed": "Could not copy cookie file: {err}",
    "settings.network.platform_cookie_saved": "{platform} cookie encrypted and saved to a secure folder.",
    "settings.network.platform_extract_failed_status": "❌ {platform}: {err}",
    "settings.network.platform_extract_failed_toast": "Failed to fetch {platform} cookies: {err}",
    "settings.network.platform_saved_status": "✓ {platform}: {count} cookie(s) saved",
    "settings.network.platform_got_toast": "Got {count} {platform} cookies from {browser}.",
    "settings.network.reading_platform_status": "Reading {platform} cookies from {browser}…",
    "settings.network.cdp_unsupported_use_browser": (
        "CDP only supports Brave/Chrome/Edge. Use 🔄 for {browser}."
    ),
    "settings.network.platform_cdp_failed_status": "❌ {platform} CDP: {err}",
    "settings.network.platform_cdp_failed_toast": "CDP {platform} failed: {err}",
    "settings.network.platform_cdp_saved_status": "✓ {platform}: {count} cookie(s) (CDP)",
    "settings.network.platform_cdp_toast": "CDP: got {count} {platform} cookies.",
    "settings.network.starting_cdp_platform_status": "Starting {browser} to fetch {platform} cookies…",
    # ── Added by the tab audit (home / cards / status bar) ───────────────
    "home.welcome_title": "Ready to download",
    "home.welcome_sub": "Paste a video link in the bar above and press Analyse",
    "home.more_platforms": "+ 1000 platforms",
    "home.loading": "Loading media info…",
    "home.loading_sub": "This may take a few seconds",
    "home.quality_section": "QUALITY",
    "home.format_label": "Format",
    "home.folder_label": "Folder",
    "home.browse": "Browse",
    "home.add_to_queue": "Add to queue",
    "home.no_preview": "No preview",
    "home.choose_dir": "Choose download folder",
    "home.photo_note": "Photo / image — downloading at best available resolution",
    "home.no_media_info": "No media information returned.",
    "home.batch_unavailable": "Batch tab is not available.",
    "home.display_error": "Display error: {err}",
    "home.added_toast": "Added: {title}",
    "home.quality.best": "Best quality",
    "home.quality.4k": "4K / 2160p",
    "home.quality.1080": "1080p Full HD",
    "home.quality.720": "720p HD",
    "home.quality.480": "480p",
    "home.quality.360": "360p",
    "home.quality.audio_mp3": "Audio only MP3",
    "home.quality.audio_m4a": "Audio only M4A",
    "item.status.queued": "Queued",
    "item.status.downloading": "Downloading",
    "item.status.processing": "Processing",
    "item.status.paused": "Paused",
    "item.status.completed": "Completed",
    "item.status.failed": "Failed",
    "item.status.cancelled": "Cancelled",
    "item.status.partial": "Partial",
    "item.status.unknown": "Unknown",
    "item.pause_tip": "Pause / Resume",
    "item.cancel_tip": "Cancel download",
    "item.open": "Open",
    "item.open_tip": "Open the containing folder",
    "item.preview": "View",
    "item.preview_tip": "View / play the file",
    "item.convert": "Convert",
    "item.convert_tip": "Convert the format",
    "item.edit": "Edit",
    "item.edit_tip": "Trim / edit the video",
    "item.send": "Send",
    "item.send_tip": "Send the file via Taildrop",
    "item.sending": "Sending…",
    "item.rename": "Rename",
    "item.rename_tip": "Rename the file",
    "pda.convert": "Convert",
    "pda.convert_compact": "Conv",
    "pda.converting": "Converting → .{ext}…",
    "pda.converting_short": "Converting…",
    "pda.done": "Done",
    "pda.send": "Send",
    "pda.sending": "Sending…",
    "pda.delete": "Delete",
    "pda.edit": "Edit",
    "pda.convert_failed": "Convert failed: {msg}",
    "pda.convert_error_file": "Convert error for {name}: {err}",
    "pda.convert_error": "Convert error: {err}",
    "pda.empty_folder": "The folder is empty.",
    "pda.pick_send_title": "Choose files to send",
    "pda.pick_send_confirm": "Send selected",
    "pda.pick_delete_title": "Choose files to delete",
    "pda.pick_delete_confirm": "Delete selected",
    "pda.confirm_delete_title": "Confirm delete",
    "pda.confirm_delete_all": "Delete all {count} file(s) from this post?",
    "pda.confirm_delete_empty_dir": "Delete the empty folder '{name}'?",
    "pda.confirm_delete_file": "Delete this file?\n{name}",
    "pda.delete_failed": "Could not delete: {err}",
    "pda.target_format": "Choose the target format:",
    "pda.fmt.custom": "Custom (quality + encoder)…",
    "pda.encoder": "Encoder:",
    "pda.quality": "Quality:",
    "pda.crf": "CRF:",
    "pda.speed": "Speed:",
    "pda.q.high": "High (CRF 18)",
    "pda.q.standard": "Standard (CRF 23)",
    "pda.q.small": "Small 720p (CRF 28)",
    "pda.q.custom": "Custom CRF",
    "pda.pick_files_label": "Choose the files to act on:",
    "pda.select_all": "Select all",
    "pda.deselect_all": "Deselect all",
    "status.idle": "No active task",
    "status.downloading": "{count} downloading",
    "status.net_ok": "Network OK",
    "status.net_down": "No connection",
    "status.eta_min": "~{value} min left",
    "status.eta_sec": "~{value}s left",
    "palette.search": "Search commands…",
    "app.quit.downloads": "{count} download(s)",
    "app.quit.conversions": "{count} conversion(s)",
    "app.quit.and": " and ",
    "app.quit.confirm": "{summary} still running.\nClose and cancel everything?",
    "settings.clipboard_on": "Clipboard monitor ON",
    "settings.clipboard_off": "Clipboard monitor OFF",
    "settings.debug_on": "Debug logging ON — writing to omnidl_debug.log",
    "settings.debug_off": "Debug logging OFF",
    "convert.cancelled": "Cancelled",
    "pda.fmt.mp4": "MP4 — H.264 / AAC (iPhone, Android)",
    "pda.fmt.mp3": "MP3 — Audio only",
    "pda.fmt.mkv": "MKV — Lossless container",
    "pda.fmt.avi": "AVI — Legacy compatibility",
    "convert.codec.h264": "H.264 (most compatible)",
    "convert.codec.hevc": "H.265 / HEVC (~30% smaller)",
    "convert.codec.av1": "AV1 (~50% smaller, needs ff8+)",
    "convert.speed.quality": "Slow (best compression)",
    "convert.speed.balanced": "Balanced",
    "convert.speed.fast": "Fast (less compression)",
    "convert.err.file_missing": "File does not exist: {path}",
    "convert.err.ffmpeg_exit": "ffmpeg exited with error {code}.\n{tail}",
    "trim.err.no_ffmpeg": "FFmpeg not found. Install FFmpeg and try again.",
    "subs.model.tiny": "Tiny (74 MB, fastest)",
    "subs.model.base": "Base (141 MB, balanced)",
    "subs.model.small": "Small (465 MB, more accurate)",
    "subs.model.medium": "Medium (1.4 GB, best)",
    "subs.lang.auto": "Auto-detect",
    "subs.err.invalid_model": "Invalid model: {model}",
    "subs.err.invalid_language": "Invalid language: {language}",
    "subs.err.no_whisper": (
        "This FFmpeg build has no whisper filter — the 'full' build is required (see README)."
    ),
    "subs.err.incomplete_download": "Incomplete model download ({got}/{total} bytes)",
    "subs.err.failed": "Subtitle generation failed: {err}",
    "subs.err.no_speech": "No speech was detected in the video.",
    # ── Engine / service messages ─────────────────────────────────────────
    # Errors and progress text raised outside the UI layer (download engines,
    # cookie extraction, live checkers).  Retry logic keys off the catalogue
    # KEY, never this text — see yt_dlp_engine._error_key().
    "err.private": "Content is private. Try enabling cookies in Settings.",
    "err.not_found": "URL not found or content was removed.",
    "err.unsupported_platform": "This platform is not supported by yt-dlp.",
    "err.live_not_started": "Live stream has not started yet.",
    "err.not_currently_live": "The channel is not currently live.",
    "err.live_ended": "Live stream has ended.",
    "err.ig_photo_only": (
        "This post only has photos, no video.\n"
        "OmniDL will retry the images with format='best'.\n"
        "If it still fails, make sure you are using Instagram cookies (not Facebook) and the latest yt-dlp."
    ),
    "err.ytdlp_internal": (
        "yt-dlp hit an internal error while parsing this URL.\n"
        "Update yt-dlp to the latest version:\n"
        "Settings → Update yt-dlp, or run: pip install -U yt-dlp"
    ),
    "err.ig_checkpoint": (
        "Instagram requires account verification.\n"
        "1. Open Instagram in a browser and finish the verification.\n"
        "2. Export fresh cookies (use the 'Get cookies.txt LOCALLY' extension).\n"
        "3. Update the cookie file in Settings → Network → Cookie file.\n"
        "Note: Instagram cookies usually expire after 1–2 weeks."
    ),
    "err.rate_limit": (
        "Rate limit reached — too many requests in a short time.\n"
        "Wait 5–10 minutes and try again. Enabling browser cookies in Settings may help."
    ),
    "err.tls_fingerprint": (
        "TLS connection error — the server rejected the default TLS fingerprint.\n"
        "OmniDL uses curl_cffi (Chrome impersonation) to work around this.\n"
        "If the error persists:\n"
        "  1. Check that no antivirus/proxy intercepts HTTPS\n"
        "  2. Try a proxy in Settings → Network → Proxy URL\n"
        "  3. Run: pip install -U curl-cffi"
    ),
    "err.fb_unavailable": (
        "This Facebook content is not available. It may require login or be restricted to a specific region."
    ),
    "err.geo_restricted": (
        "This content is geo-restricted and not available in your region.\n"
        "Try enabling a VPN or proxy in Settings → Network → Proxy URL."
    ),
    "err.ffmpeg_livestream": (
        "Could not record the live stream — ffmpeg reported an error.\n"
        "Common causes:\n"
        "  • The live URL expired (TikTok URLs expire after ~1–2 minutes)\n"
        "    → Copy the link again and start the download immediately\n"
        "  • The stream ended or was paused\n"
        "  • The network connection dropped while recording\n"
        "If the error persists: re-copy the link or wait for the stream to stabilise."
    ),
    "err.ip_blocked": (
        "Your IP is blocked by TikTok/the platform for this post.\n"
        "Common causes:\n"
        "  • The IP was blacklisted after too many requests (temporary rate limit)\n"
        "  • An ISP/VPS/datacenter IP blocked by a geo policy\n"
        "Fixes:\n"
        "  1. Enable a proxy/VPN in Settings → Network → Proxy URL\n"
        "     (e.g. socks5://127.0.0.1:1080 for a local proxy)\n"
        "  2. Wait 5–15 minutes and try again (if it is a temporary rate limit)\n"
        "  3. Refresh the TikTok cookie: Settings → Per-Platform Cookies → TikTok"
    ),
    "err.tiktok_login_required": (
        "TikTok requires a login to download this video.\n"
        "The cookie pool may have expired or belongs to a different account.\n"
        "Fix: refresh the TikTok cookie in Settings → Per-Platform Cookies → TikTok"
    ),
    "err.copyright": "This content has been blocked due to a copyright claim.",
    "err.blocked": (
        "This content is blocked or access was denied.\n"
        "Try enabling a VPN or proxy in Settings → Network → Proxy URL."
    ),
    "err.account_suspended": "The account that posted this content has been suspended.",
    "err.members_only": (
        "This content is for members/subscribers only.\n"
        "Make sure you are logged in via cookies in Settings."
    ),
    "err.tiktok_10231": (
        "The TikTok API rejected the request (status 10231) even though the video is viewable.\n"
        "Try:\n"
        "  1. Refresh the TikTok cookie: Settings → Per-Platform Cookies → TikTok\n"
        "  2. Enable a proxy/VPN in Settings → Network → Proxy URL"
    ),
    "err.video_deleted": (
        "This video no longer exists or was removed.\n"
        "Check the URL — for a short link (vt.tiktok.com), open it in a browser to get the full URL."
    ),
    "err.threads_unsupported": (
        "Threads posts are not supported by yt-dlp yet.\n"
        "\n"
        "How to save a Threads video:\n"
        "• Open the post in a browser → tap ... → Save\n"
        "• Or use a 'Video Downloader' browser extension."
    ),
    "err.ig_stories_cookies": (
        "Instagram Stories require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file."
    ),
    "err.ig_live_cookies": (
        "Instagram Live streams require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file."
    ),
    "err.fb_live_cookies": (
        "Facebook Live streams require cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file."
    ),
    "err.fb_stories_manual": (
        "Facebook Stories cannot be downloaded automatically.\n"
        "\n"
        "How to save a Facebook Story:\n"
        "• Open the Story in a browser → tap ... → Save video\n"
        "• Or use a 'Video Downloader' browser extension."
    ),
    "err.ig_cookie_expired": "Instagram cookie expired — refresh the cookie in Settings.",
    "err.playlist_failed": "Could not read the list from this URL: {err}",
    "err.no_data": "No data returned for this URL. Check the URL or add a cookie file in Settings.",
    "err.playlist_empty": (
        "This playlist/profile has no available videos.\n"
        "The account may be private, or a cookie file may be required."
    ),
    "err.ffmpeg_not_found": "FFmpeg not found. Check your FFmpeg installation.",
    "err.ffmpeg_stall": "FFmpeg stall watchdog: no data for 120 seconds — the stream may have ended.",
    "err.no_error_detail": "No error details available.",
    "err.hls_stall": (
        "Stall watchdog: curl_cffi HLS received no new segment for {seconds}s — the stream may have ended."
    ),
    "err.livestream_ended_relink": (
        "The live stream ended or the HLS URL is no longer valid.\n"
        "Add the link again to watch for the next broadcast."
    ),
    "err.tiktok_audio_only": (
        "This item has audio only — there is no video track.\n"
        "TikTok product/showcase and \"template effect\" / AR effect videos expose no video track through the"
        " "
        "API (audio stream only).\n"
        "How to save it: open the video in the TikTok app → Share → Save video."
    ),
    "err.tiktok_ec_blocked": (
        "This video cannot be downloaded — TikTok blocks the video URL entirely.\n"
        "It is an e-commerce/product video (isECVideo=1): TikTok exposes no video URL to any API client.\n"
        "How to save it: open the video in the TikTok app → Share → Save video."
    ),
    "err.no_username_from_url": "Could not read a username from the URL.",
    "err.no_username_from_tiktok_url": "Could not read a username from the TikTok URL.",
    "err.invalid_url_scheme": "Invalid URL — it must start with http:// or https://",
    "err.monitor_limit": "The limit of {count} URLs has been reached.",
    "err.already_monitored": "This URL is already being monitored.",
    "err.profile_watch_needs_ig_cookie": (
        "The profile watcher needs an Instagram cookie file. Set one up in Settings → Network → Cookie file."
    ),
    "err.check_stuck": "The check hung. Retry, or check your network connection.",
    "err.attempts_failed": "{count} attempts failed. Last error: {err}",
    "err.download_failed": "Download failed",
    "err.network": "Network error: {err}",
    "err.http": "HTTP error: {err}",
    "err.cookie_unreadable": "Could not read the cookie file: {err}",
    "err.ig_cookie_required": (
        "An Instagram cookie file is required to check live status.\n"
        "Set one up in Settings → Network → Cookie file."
    ),
    "err.ig_cookie_no_sessionid": (
        "The cookie file has no Instagram sessionid.\n"
        "Export the cookie file again after logging in to Instagram."
    ),
    "err.ig_cookie_invalid": (
        "The Instagram cookie expired or is invalid.\n"
        "Refresh the cookie file in Settings → Network."
    ),
    "err.ig_account_not_found": "Account @{username} was not found.",
    "err.ig_rate_limited": (
        "Rate limited — Instagram is blocking temporarily.\n"
        "Wait 5–10 minutes and try again."
    ),
    "err.ig_forbidden": "Access denied (403). The cookie may have expired.",
    "err.ig_api_timeout": "The Instagram API timed out. Try again later.",
    "err.ig_bad_response": (
        "Instagram returned an invalid response. Try again later, or check the cookie file."
    ),
    "err.tiktok_api_timeout": "The TikTok API timed out. Try again later.",
    "err.no_username_from_facebook_url": "Could not read a username from the Facebook URL.",
    "err.profile_watch_needs_fb_cookie": (
        "Watching a Facebook page needs a Facebook cookie file. "
        "Set one up in Settings → Network → Cookie file."
    ),
    "err.fb_cookie_required": (
        "A Facebook cookie file is required to check live status.\n"
        "Set one up in Settings → Network → Cookie file."
    ),
    "err.fb_cookie_no_session": (
        "The cookie file has no Facebook session (c_user/xs missing).\n"
        "Export the cookie file again after logging in to Facebook."
    ),
    "err.fb_cookie_invalid": (
        "The Facebook cookie expired or is invalid.\n"
        "Refresh the cookie file in Settings → Network."
    ),
    "err.fb_page_not_found": "Facebook page {username} was not found.",
    "err.fb_rate_limited": (
        "Rate limited — Facebook is blocking temporarily.\n"
        "Wait 5–10 minutes and try again."
    ),
    "err.fb_api_timeout": "Facebook timed out. Try again later.",
    "progress.recorded": "⏺ {size} recorded",
    "cookie.err.app_bound_encryption": (
        "Brave/Chrome 127+ use App-Bound Encryption — cookies cannot be read\n"
        "from outside the browser. This is a Windows/Chrome security limit, not a bug.\n"
        "\n"
        "✅ Fastest route: use Firefox\n"
        "   1. Open Firefox and log in to TikTok/Instagram/...\n"
        "   2. Switch the dropdown to firefox → click 🔄\n"
        "\n"
        "📁 Or export manually from Brave:\n"
        "   Install the Cookie-Editor extension → Export → Netscape format\n"
        "   → Settings → Browse… → pick the exported .txt file"
    ),
    "cookie.err.brave_locked": (
        "Brave is open — the cookie database is locked.\n"
        "⚠ Close Brave completely (including the background process in the System Tray),\n"
        "then click 🔄 again. You can reopen Brave once the cookies are extracted."
    ),
    "cookie.err.db_missing_detected": (
        "No cookie database found for '{browser}'.\n"
        "⚠ Pick the browser you actually use in the\n"
        "'Cookie source browser' dropdown, then click 🔄 again."
    ),
    "cookie.err.db_missing": (
        "No cookie database found for the selected browser.\n"
        "⚠ Pick the browser you actually use in the\n"
        "'Cookie source browser' dropdown, then click 🔄 again."
    ),
    "cookie.err.db_locked": (
        "Cannot read cookies — the browser is open and holds the database lock.\n"
        "Close the browser completely (including background processes) and try again."
    ),
    "cookie.err.decrypt_failed": (
        "Could not decrypt the browser cookies.\n"
        "Try running OmniDL as Administrator, or pick a different browser."
    ),
    "cookie.err.profile_missing": (
        "No profile found for the selected browser.\n"
        "⚠ Re-check the 'Cookie source browser' dropdown — pick the browser\n"
        "you actually use (e.g. Brave ≠ Chrome)."
    ),
    "cookie.err.permission_denied": (
        "Access to the browser cookie file was denied.\n"
        "Try running OmniDL as Administrator."
    ),
    "cookie.err.browser_unsupported": (
        "This browser is not supported by yt-dlp.\n"
        "Try Chrome, Firefox or Edge."
    ),
    "cookie.err.cdp_no_connection": (
        "Could not connect to the browser.\n"
        "Try again — the first launch can take a few seconds."
    ),
    "cookie.err.cdp_websocket": "WebSocket connection to the browser failed.\nTry again, or restart OmniDL.",
    "cookie.err.cdp_no_cookies": (
        "No cookies came back from the browser. Log in to the sites, then try again."
    ),
    "cookie.err.read_failed": (
        "Could not read cookies — the browser may not be logged in, or the database is locked. Try closing "
        "the browser completely."
    ),
    "cookie.err.browser_empty": (
        "The browser has no cookies. Log in to the sites you want to download from first."
    ),
    "cookie.err.platform_empty": (
        "No {platform} cookies found in the browser.\n"
        "Make sure you are logged in to {platform} in that browser."
    ),
    "cookie.err.platform_empty_browser": (
        "No {platform} cookies found in {browser}.\n"
        "Make sure you are logged in to {platform} in {browser}."
    ),
    "cookie.err.cdp_windows_only": (
        "CDP mode (🦁) currently supports Windows only.\n"
        "On macOS/Linux use the 🔄 button (yt-dlp) or pick a cookie file manually."
    ),
    "cookie.err.browser_not_installed": (
        "{browser} was not found on this machine.\n"
        "Check that Brave/Chrome is installed."
    ),
    "cookie.err.cdp_zero_cookies": (
        "The browser returned 0 cookies.\n"
        "Log in to the sites before extracting cookies."
    ),
    "cookie.err.cdp_timeout": (
        "Could not connect to {browser} within 20 seconds.\n"
        "Try again — the first launch can take longer."
    ),
    "cookie.err.browser_running": (
        "{browser} is running — it must be closed briefly to read the cookies.\n"
        "\n"
        "⚠ Close {browser} completely (including the System Tray),\n"
        "then click 🦁 again. You can reopen it once the cookies are extracted.\n"
        "\n"
        "Why: CDP reads the real profile, and {browser} currently holds its lock."
    ),
    "cookie.err.profile_dir_missing": (
        "No profile directory found for {browser}.\n"
        "Check that Brave/Chrome is installed and has been logged in at least once."
    ),
    "err.gdl_login_required": (
        "gallery-dl requires a login.\n"
        "Check the cookie file in Settings → Network → Cookie file.\n"
        "Make sure it is an Instagram cookie (not Facebook)."
    ),
    "err.gdl_rate_limited": (
        "gallery-dl was rate limited — Instagram is blocking temporarily.\n"
        "Wait 5–10 minutes and try again."
    ),
    "err.gdl_not_installed_short": "gallery-dl is not installed.\nRun: pip install gallery-dl",
    "err.gdl_private": "This content is private —\na cookie for an account that can view it is required.",
    "err.gdl_unknown": "gallery-dl failed for an unknown reason.",
    "err.gdl_not_installed": (
        "gallery-dl is not installed.\n"
        "Run: pip install gallery-dl\n"
        "Then restart OmniDL."
    ),
    "err.gdl_no_content": "gallery-dl found no content at this URL.\nCheck the URL, or refresh the cookie.",
    "err.fb_post_advert_only": (
        "This Facebook post holds photos only.\n"
        "The one video yt-dlp found is an advert Facebook injected into the page, "
        "not the post itself.\n"
        "Refresh the Facebook cookie in Settings -> Network and try again."
    ),
    "err.gdl_timeout": "gallery-dl timed out while reading the URL info.",
    "err.gdl_missing_binary": "gallery-dl was not found.\nInstall it with: pip install gallery-dl",
    "gdl.photo_count": "{count} photos",
    "progress.gdl_preparing": "⬇ Preparing the image download…",
    "progress.gdl_video_audio": "⬇ Downloading the video with audio…",
    "progress.gdl_downloaded": "⬇ {count} files downloaded",
    "err.cdn_file_too_small": "The downloaded file is too small — the CDN link may have expired.",
    "err.cdn_link_expired": (
        "The CDN link expired (the oe= signature is stale). Paste a fresh link from the browser."
    ),
    "err.cdn_forbidden": "Access to the CDN link was denied (403).",
    "err.ks_strategy_e_platform": (
        "Kuaishou: the last-resort fallback (Strategy E) supports Windows and macOS only.\n"
        "\n"
        "Set up a Kuaishou cookie in Settings → Network to enable the other extraction strategies."
    ),
    "err.ks_all_strategies_failed": (
        "Kuaishou: every extraction strategy failed.\n"
        "\n"
        "Possible causes:\n"
        "• The video was deleted or is private\n"
        "• Kuaishou blocks requests from your current IP\n"
        "• The Kuaishou page structure changed\n"
        "\n"
        "Set up a Kuaishou cookie in Settings → Network to enable more extraction strategies."
    ),
    "err.ks_no_video_url": (
        "Kuaishou: no video URL found in the page data.\n"
        "The video may be region-restricted, or the API changed."
    ),
    "err.ks_bad_file": (
        "The downloaded file is invalid (not MP4, or too small). The CDN URL may have expired — try again."
    ),
    "err.ks_no_photo_id": "Could not extract photo_id from the URL: {url}\nCheck the Kuaishou URL.",
    "err.ks_cancelled": "Kuaishou: cancelled.",
    "err.ks_no_browser": (
        "Kuaishou: neither Brave nor Chrome was found on this machine.\n"
        "\n"
        "The last-resort fallback (Strategy E) needs one of them to open the Kuaishou page and intercept the "
        "real CDN link.\n"
        "\n"
        "Install Brave (https://brave.com) or Google Chrome, then try again.\n"
        "No further configuration is needed — OmniDL finds it automatically."
    ),
    "err.ks_cdn_http": "The Kuaishou CDN returned HTTP {code}. The CDN URL may have expired — try again.",
    "err.ks_cdn_http_after_reextract": (
        "The Kuaishou CDN returned HTTP {code} after re-extraction. The CDN URL may have expired — try again."
    ),
    "err.ks_cdn_html_after_reextract": (
        "The Kuaishou CDN still returns HTML after re-extraction — the IP is blocked, or the video is gone."
    ),
    "err.ks_cdn_html_no_page_url": (
        "The Kuaishou CDN returned HTML but the page URL for re-extraction could not be determined.\n"
        "The Remote API must send the Kuaishou page URL, not the CDN URL."
    ),
    "err.ks_no_page_url": (
        "Kuaishou: could not determine the page URL for re-extraction.\n"
        "task.url looks like a CDN URL (video_id={video_id}) — the Remote API must send the page URL, not the"
        " "
        "CDN URL."
    ),
    "err.waaw_cdn_expired": (
        "The CDN link expired or is IP-locked - reopen the waaw.ac/f/... page to get a fresh link"
    ),
    "err.waaw_platform": "The waaw.ac engine requires Windows or macOS.",
    "err.waaw_platform_linux": "The waaw.ac engine requires Windows or macOS.\nLinux is not supported yet.",
    "err.waaw_file_too_small": "The downloaded file is too small (< 10 KB) — the CDN may have blocked it.",
    "err.waaw_not_mp4": "The downloaded file is not an MP4 — the CDN URL may have expired.",
    "err.waaw_hls_no_fallback": "The HLS stream could not be downloaded and there is no MP4 fallback URL.",
    "err.waaw_hls_failed": "Could not download the HLS stream (ffmpeg: {tail}; MP4 fallback: {err})",
    "err.waaw_captcha_timeout": (
        "The captcha was not solved in time.\n"
        "Try again and solve the captcha in the browser window that opens, or paste a fresh CDN link obtained"
        " "
        "elsewhere (e.g. cf*cdn.com .m3u8)."
    ),
    "err.waaw_no_cdn_url": (
        "No CDN URL found after {seconds} seconds.\n"
        "waaw.ac may have changed its protection scheme."
    ),
    "err.generic_error_word": "error",
    "err.cancelled_by_user": "Cancelled by user.",
    "err.playwright_missing": "The Playwright library is missing.\nRun: pip install playwright",
    "err.cdp_connect_failed": (
        "Could not connect over CDP.\n"
        "Close the browser completely and try again.\n"
        "(details: {err})"
    ),
    "err.cdp_connect_failed_hard": (
        "Could not connect over CDP.\n"
        "\n"
        "Close the browser COMPLETELY (including the System Tray) and try again.\n"
        "(details: {err})"
    ),
    "err.browser_not_found_cdp": (
        "{browser} was not found.  Install the browser first.\n"
        "Note: App Store builds do not support CDP — use the build from the official website."
    ),
    "err.fb_not_story_url": (
        "This is not a Facebook Story URL.\n"
        "Paste a URL of the form facebook.com/stories/..."
    ),
    "err.no_download_dir": "The download folder has not been set",
    "err.fb_story_platform": "Facebook Story supports Windows and macOS only.\nLinux is not supported yet.",
    "err.fb_story_browser_running": (
        "{browser} is already running. Close {browser} completely, then press Download again.\n"
        "Why: the browser only runs one instance per profile, so OmniDL cannot open "
        "its debugging port while {browser} is up."
    ),
    "err.fb_story_no_video_url": (
        "The Story's video URL could not be captured.\n"
        "\n"
        "Possible causes:\n"
        "• The Story expired (Stories last 24 hours)\n"
        "• You are not logged in to Facebook in Brave/Chrome\n"
        "• This Story is photo-only (no video)\n"
        "\n"
        "Open the Story in a browser to check first."
    ),
    "err.fb_story_incomplete_download": (
        "The video URL was captured but the file did not download completely.\n"
        "\n"
        "Common causes:\n"
        "• The CDN URL expired (the page took too long to load)\n"
        "• An unstable network connection\n"
        "\n"
        "Try again right after opening the Story in a browser."
    ),
    "progress.browser_start": "Starting the browser...",
    "progress.browser_start_named": "Starting {browser}...",
    "progress.cdp_connect": "Connecting over CDP...",
    "progress.waaw_open_page": "Opening the waaw.ac page...",
    "progress.waaw_wait_cdn": "Waiting for the CDN URL...",
    "progress.waaw_captcha": (
        "The page asks for a captcha — solve it in the browser window that just opened..."
    ),
    "progress.ffmpeg_hls": "ffmpeg is downloading the HLS stream...",
    "progress.ffmpeg_dash": "ffmpeg is processing the DASH stream...",
    "progress.ffmpeg_dash_fetch": "FFmpeg is fetching video+audio from DASH...",
    "progress.ffmpeg_merge": "FFmpeg is merging video + audio...",
    "progress.ks_browser_start": "Kuaishou: starting the browser...",
    "progress.ks_cdp_connect": "Kuaishou: connecting over CDP...",
    "progress.ks_open_page": "Kuaishou: opening the video page...",
    "progress.fb_open_story": "Opening the Story in the browser...",
    "progress.wait_video": "Waiting for the video to load...",
    "progress.url_captured": "URL captured — downloading...",
    "progress.downloading_video": "Downloading the video...",
    "progress.downloading_kb": "Downloading... {kb} KB",
    "progress.done_named": "✅ Done! {name}",
    "nav.docs": "Documents",
    "docs.title": "Document Convert",
    "docs.subtitle": "Markdown ↔ PDF · HTML ↔ PDF · Office ↔ PDF",
    "docs.add_file": "Add document",
    "docs.remove_selected": "Remove selected",
    "docs.clear": "Clear all",
    "docs.source_label": "Source documents:",
    "docs.target_label": "Target format:",
    "docs.save_dir_label": "Save to:",
    "docs.choose": "Choose...",
    "docs.convert": "Convert",
    "docs.cancel": "Cancel",
    "docs.page_n": "Page {n}",
    "docs.file_filter": "Documents ({exts});;All files (*)",
    "docs.no_file_title": "No document selected",
    "docs.no_file_msg": "Add at least one document to convert.",
    "docs.no_target_title": "No shared target format",
    "docs.no_target_msg": (
        "The selected documents have no target format in common. "
        "Convert them in separate batches."
    ),
    "docs.status.converting": "Converting {name}... ({i}/{total})",
    "docs.status.done": "Done: {ok}/{total} document(s) → {dir}",
    "docs.status.failed": "Conversion failed",
    "docs.status.cancelled": "Conversion cancelled",
    "docs.done_title": "Finished",
    "docs.done_msg": "Converted {ok}/{total} document(s).\nOpen the output folder?",
    "docs.error_title": "Conversion error",
    "docs.caps_title": "What this machine can do",
    "docs.cap.markdown": "Markdown → HTML (markdown package)",
    "docs.cap.weasyprint": "HTML → PDF (WeasyPrint)",
    "docs.cap.pypdf": "PDF → text (pypdf)",
    "docs.cap.libreoffice": "Office ↔ PDF (LibreOffice)",
    "docs.cap_ok": "Ready",
    "docs.cap_missing": "Not installed",
    "docs.libreoffice_hint": (
        "Install LibreOffice to enable Office ↔ PDF conversion (.docx, .xlsx, .pptx, .odt...)."
    ),
    "docs.recheck": "Re-check",
    "docs.weasyprint_hint": (
        "The PDF rendering libraries (Pango/GTK) were not found. On Windows, install "
        "the GTK for Windows Runtime to enable Markdown/HTML → PDF."
    ),
}

ZH: dict[str, str] = {
    "nav.home": "下载",
    "nav.queue": "队列",
    "nav.batch": "批量",
    "nav.live_monitor": "直播",
    "nav.convert": "转换",
    "nav.editor": "编辑器",
    "nav.archive": "压缩/解压",
    "nav.history": "历史记录",
    "nav.settings": "设置",
    "nav.special_dl": "特殊下载",
    "nav.section.download": "下载",
    "nav.section.tools": "工具",
    "nav.section.library": "资料库",
    "nav.section.system": "系统",
    "topbar.theme_light": "☀ 浅色",
    "topbar.theme_dark": "🌙 深色",
    "topbar.notifications": "通知",
    "topbar.hide_nav": "隐藏导航栏",
    "topbar.nav_strip": "导航栏",
    "topbar.toolbar_strip": "解析栏",
    "topbar.theme_changed": "主题已更改 — 重启应用后在所有标签页生效。",
    "toolbar.url_placeholder": "在此粘贴视频链接，然后按 Enter 或点击“解析”...",
    "toolbar.analyse": "解析",
    "toolbar.analysing": "正在解析",
    "toolbar.analysing_dots": "正在解析...",
    "toolbar.stop": "停止",
    "toolbar.paste_tip": "从剪贴板粘贴",
    "toolbar.clear_tip": "清除网址",
    "toolbar.collapse_tip": "隐藏解析栏",
    "toolbar.status.clipboard": "来自剪贴板的网址 - 按 Enter 解析",
    "toolbar.status.loading": "正在加载媒体信息...",
    "toolbar.status.cancelled": "已取消。",
    "toolbar.status.no_media": "未找到媒体",
    "toolbar.status.pasted": "已粘贴网址",
    "toolbar.toast.no_result": "解析没有返回结果。",
    "toolbar.toast.ready": "就绪：{title}",
    "toolbar.toast.failed": "解析失败。",
    "toolbar.error.unknown": "未知错误",
    "queue.title": "下载队列",
    "queue.select": "选择",
    "queue.select_tip": "选择多个任务",
    "queue.clear_finished": "清除已完成",
    "queue.clear_selected": "清除已选 ({count})",
    "queue.empty": "暂无任务\n在“下载”标签页粘贴网址即可开始",
    "queue.count": "  {active} 个下载中  ·  共 {total} 个  ",
    "queue.rename_title": "重命名文件",
    "queue.rename_label": "新文件名：",
    "queue.no_target": "尚未在 设置 → Taildrop 中配置目标设备",
    "queue.taildrop_off": "尚未在设置中启用 Taildrop",
    "queue.sent_to": "📲  已发送 → {node}",
    "queue.send_failed": "❌  发送失败 → {node}：{err}",
    "queue.sending": "📲  正在发送到 {count} 台设备：{nodes}",
    "queue.send_error": "❌  文件发送错误：{err}",
    "settings.search": "搜索设置...",
    "settings.no_results": "没有匹配的设置。",
    "settings.section.location": "保存位置",
    "settings.section.behaviour": "下载行为",
    "settings.section.appearance": "外观",
    "settings.section.clipboard": "剪贴板监听",
    "settings.section.developer": "开发者选项",
    "settings.browse": "浏览...",
    "settings.max_concurrent": "最大同时下载数",
    "settings.restart_hint": "⚠  重启应用后生效。",
    "settings.max_retries": "出错时的重试次数",
    "settings.embed_thumbnail": "嵌入缩略图",
    "settings.embed_metadata": "嵌入元数据",
    "settings.theme": "配色主题",
    "settings.language": "语言",
    "settings.language_hint": "主界面立即生效；其余界面在重启后更新。",
    "settings.language_changed": "语言已切换为 {language}",
    "settings.clipboard_watch": "自动检测剪贴板中的网址",
    "settings.clipboard_hint": "每 1.5 秒检查一次剪贴板并自动填入网址栏",
    "settings.debug_logging": "详细日志 (debug)",
    "settings.debug_hint": "详细内容写入 omnidl_debug.log  •  无需重启",
    "settings.folder_not_found": "找不到文件夹，请重新选择。",
    "settings.folder_create_failed": "无法创建文件夹：{err}",
    "settings.select_folder": "选择下载文件夹",
    "history.title": "下载历史",
    "history.clear_all": "全部清除",
    "history.search_placeholder": "按标题、网址或文件名搜索...",
    "history.filter.all": "全部",
    "history.status.completed": "已完成",
    "history.status.failed": "失败",
    "history.status.cancelled": "已取消",
    "history.load_more": "加载更多（还剩 {remaining} 项）",
    "history.empty_no_results": "未找到结果",
    "history.empty_no_history": "暂无下载历史",
    "history.redownload": "重新下载",
    "history.redownload_tip": "重新下载",
    "history.copy_url": "复制网址",
    "history.copy_url_tip": "复制网址",
    "history.folder": "文件夹",
    "history.folder_tip": "打开文件所在文件夹",
    "history.rename": "重命名",
    "history.rename_tip": "重命名文件",
    "history.delete": "删除",
    "history.delete_tip": "从历史记录中删除",
    "history.clear_confirm": "清除全部下载历史？",
    "special.title": "特殊下载",
    "special.subtitle": "下载 yt-dlp 不支持的内容 — Facebook Story 等",
    "special.platform_label": "平台：",
    "special.browser_label": "浏览器：",
    "special.url_placeholder.facebook_story": "https://www.facebook.com/stories/...",
    "special.guide.facebook_story": (
        "使用说明：\n"
        "1. 完全关闭 Brave / Chrome（包括系统托盘 / Dock）\n"
        "2. 将 Story 网址粘贴到上方输入框\n"
        "3. 选择浏览器 → 点击 下载\n"
        "4. 浏览器会自动打开，下载完成后自动结束\n"
        "Story 会在 24 小时后过期\n"
        "请使用官网安装包 — App Store 版本不支持"
    ),
    "special.download_btn": "下载",
    "special.downloading": "下载中...",
    "special.status.waiting": "等待中...",
    "special.status.starting": "正在启动...",
    "special.retry": "重试",
    "special.clear_history": "清除历史",
    "special.open_folder": "打开文件夹",
    "special.view": "查看",
    "special.error.paste_url": "请在上方粘贴网址。",
    "special.error.not_story_url": (
        "这不是 Facebook Story 链接。\n"
        "Story 形如 facebook.com/stories/…… — fb.watch 链接和普通视频请在“下载”标签页下载。"
    ),
    "special.downloaded": "已下载：{name}",
    "special.no_result": "没有结果。",
    "special.error_prefix": "错误：{msg}",
    "special.convert_unavailable": "转换服务不可用。",
    "special.converting": "正在转换为 .{ext}…",
    "special.convert_done": "转换完成：{name}",
    "special.sent_to": "已发送 → {node}",
    "special.send_failed": "发送失败 → {node}：{err}",
    "special.sending": "正在发送到：{nodes}",
    "special.send_error": "发送错误：{err}",
    "special.file_deleted": "文件已删除。",
    "archive.title": "压缩 / 解压",
    "archive.mode.compress": "压缩",
    "archive.mode.extract": "解压",
    "archive.cancel": "取消",
    "archive.add_file": "添加文件",
    "archive.add_folder": "添加文件夹",
    "archive.remove_selected": "删除所选",
    "archive.format_label": "格式：",
    "archive.compress_individually": "分别压缩每个文件",
    "archive.encrypt_names": "加密文件名（仅 7z）",
    "archive.password_label": "密码：",
    "archive.password_placeholder": "留空则不设置密码",
    "archive.name_label": "压缩包名称：",
    "archive.use_orig_name": "使用原始名称",
    "archive.use_orig_name_tip": "仅当恰好选择 1 个文件/文件夹且未启用“分别压缩每个文件”时可用",
    "archive.save_dir_label": "保存文件夹：",
    "archive.choose": "选择...",
    "archive.file_label": "压缩文件：",
    "archive.extract_dest_label": "解压到：",
    "archive.view_contents": "查看内容",
    "archive.no_file_title": "未选择文件",
    "archive.no_file_compress_msg": "请至少添加一个要压缩的文件或文件夹。",
    "archive.no_file_extract_msg": "请选择要解压的压缩文件。",
    "archive.no_file_list_msg": "请选择压缩文件。",
    "archive.status.compressing": "正在压缩...",
    "archive.status.extracting": "正在解压...",
    "archive.status.listing": "正在读取内容...",
    "archive.status.compress_done": "已将 {count} 个压缩包压缩到 {dir}",
    "archive.status.compress_cancelled": "已取消压缩",
    "archive.status.compress_failed": "压缩失败",
    "archive.status.extract_done": "已解压 {count} 个文件（{size}）到 {dir}",
    "archive.status.extract_cancelled": "已取消解压",
    "archive.status.extract_failed": "解压失败",
    "archive.status.list_count": "压缩包中共 {count} 项",
    "archive.status.list_failed": "无法读取内容",
    "archive.done_title": "完成",
    "archive.compress_done_msg": "已创建 {count} 个压缩包。\n是否打开所在文件夹？",
    "archive.extract_done_msg": "解压完成。\n是否打开文件夹？",
    "archive.error_compress_title": "压缩错误",
    "archive.error_extract_title": "解压错误",
    "archive.error_title": "错误",
    "archive.choose_file": "选择文件",
    "archive.choose_folder": "选择文件夹",
    "archive.choose_save_folder": "选择保存文件夹",
    "archive.choose_archive_file": "选择压缩文件",
    "archive.choose_extract_folder": "选择解压文件夹",
    "batch.title": "批量下载",
    "batch.hint": "在此粘贴网址——每行一个（最多 {max} 个）",
    "batch.import_txt": "导入 .txt",
    "batch.clear_all": "全部清除",
    "batch.results_title": "解析结果",
    "batch.select_all": "全选",
    "batch.sequential": "顺序下载",
    "batch.quality_label": "画质：",
    "batch.retry_errors": "重试失败项",
    "batch.retry_count": "重试 {count} 个失败项",
    "batch.queue_all": "全部添加",
    "batch.queue_count": "加入队列 {count} 个视频",
    "batch.queue_add_count": "添加 {count} 个视频",
    "batch.empty": "暂无网址——请在上方粘贴链接或导入 .txt 文件",
    "batch.analysing_dots": "正在解析…",
    "batch.analyse_again": "重新解析",
    "batch.retrying_dots": "重试中…",
    "batch.no_valid_urls": "没有有效网址（{errors} 个错误）",
    "batch.ready_summary": "✓  {ready}/{total} 就绪{err_note}",
    "batch.ready_err_note": "  ·  {errors} 个错误",
    "batch.retrying_urls": "正在重试 {count} 个失败的网址…",
    "batch.analysis_failed": "解析失败",
    "batch.import_dialog_title": "导入网址列表",
    "batch.import_read_error": "无法读取文件：{err}",
    "batch.import_no_valid": "文件中未找到有效网址。",
    "batch.import_done": "已导入 {count} 个网址",
    "batch.import_capped": "（上限 {max}，跳过 {skipped}）",
    "batch.import_invalid": "· 跳过 {count} 行无效内容",
    "batch.url_count": "{count} 个网址",
    "batch.url_count_capped": "{count} 个网址——仅使用前 {max} 个",
    "batch.queued_toast": "已将 {count} 个视频加入队列。",
    "batch.queue_added": "✓  已加入队列",
    "batch.queue_added_summary": "✓  已将 {count} 个视频加入队列",
    "batch.downloading_sequential": "⬇  正在顺序下载…",
    "batch.sequential_status": "顺序下载：{count} 个视频",
    "batch.sequential_toast": "正在顺序下载——还剩 {remaining} 个视频。",
    "batch.sequential_done_btn": "✓  下载完成",
    "batch.sequential_done_status": "✓  顺序下载完成",
    "batch.playlist_label_default": "播放列表",
    "batch.playlist_loaded": "已将 {label} 中的 {count} 个视频加载到批量下载。",
    "batch.queue_all_failed": "未能加入任何视频（{count} 个错误）",
    "batch.cancel_analyse": "停止解析",
    "batch.analyse_stopped": "已停止解析",
    "batch.analysing_progress": "正在解析 {done}/{total}…",
    "editor.empty": "尚无视频。打开文件，或从队列标签页点击“编辑”。",
    "editor.hide_panel": "⊡ 隐藏面板",
    "editor.show_panel": "⊞ 显示面板",
    "editor.open_file": "📂  打开文件",
    "editor.close_file": "✕  关闭文件",
    "editor.close_file_tip": "关闭当前文件",
    "editor.set_in": "[ 设置入点",
    "editor.set_out": "设置出点 ]",
    "editor.in_label": "入点：{time}",
    "editor.out_label": "出点：{time}",
    "editor.out_placeholder": "出点：--:--",
    "editor.rotate_label": "旋转：",
    "editor.rotate.none": "不旋转",
    "editor.rotate.cw90": "顺时针 90°",
    "editor.rotate.ccw90": "逆时针 90°",
    "editor.rotate.180": "180°",
    "editor.mute": "静音",
    "editor.preview_btn": "▶ 预览",
    "editor.export_btn": "✂  导出",
    "editor.speed_label": "速度：",
    "editor.volume_label": "音量：",
    "editor.text_label": "文字：",
    "editor.text_placeholder": "叠加在视频上的文字...",
    "editor.position_label": "位置：",
    "editor.pos.top": "顶部",
    "editor.pos.middle": "居中",
    "editor.pos.bottom": "底部",
    "editor.size_label": "大小：",
    "editor.color_label": "颜色：",
    "editor.color.white": "白色",
    "editor.color.black": "黑色",
    "editor.color.yellow": "黄色",
    "editor.color.red": "红色",
    "editor.box": "背景框",
    "editor.shadow": "阴影",
    "editor.effects_title": "视频效果",
    "editor.hide": "- 隐藏",
    "editor.show": "+ 显示",
    "editor.brightness": "亮度：",
    "editor.contrast": "对比度：",
    "editor.saturation": "饱和度：",
    "editor.hue": "色调：",
    "editor.blur": "模糊：",
    "editor.fade_in": "淡入：",
    "editor.fade_out": "淡出：",
    "editor.creating_preview_btn": "⏳ 正在生成...",
    "editor.creating_preview_status": "正在生成预览...",
    "editor.back_to_original": "← 返回原片",
    "editor.previewing_status": "预览中（10 秒）",
    "editor.preview_error": "预览出错：{msg}",
    "editor.export_cancel_btn": "取消",
    "editor.exporting_status": "正在导出...",
    "editor.in_after_out_error": "入点必须早于出点",
    "editor.export_saved": "已保存：{name}",
    "editor.export_error": "错误：{msg}",
    "editor.choose_video_file": "选择视频文件",
    "editor.all_files_filter": "全部",
    "live.title": "直播监控",
    "live.hint": "粘贴直播网址——Instagram、YouTube、TikTok、Facebook、Twitch 等（最多 {max} 个）",
    "live.add_btn": "添加",
    "live.check_interval_label": "检查间隔：",
    "live.seconds_label": "秒",
    "live.pause": "暂停",
    "live.resume": "继续",
    "live.empty": "尚未监控任何网址\n请在上方粘贴直播网址以开始自动录制",
    "live.state.waiting": "等待开播",
    "live.state.checking": "检查中…",
    "live.state.live": "正在直播",
    "live.state.recording": "录制中",
    "live.state.ended": "录制完成",
    "live.check_now": "检查",
    "live.open": "打开",
    "live.convert_btn": "转换",
    "live.invalid_url": "网址无效——必须以 http:// 或 https:// 开头",
    "live.limit_reached": "已达到 {max} 个网址的上限。",
    "live.already_watching": "该网址已在监控中。",
    "live.ig_cookie_needed": "监控账号需要 Instagram cookie 文件。\n请在 设置 → 网络 → Cookie 文件 中配置。",
    "live.watching_toast": "正在监控 {label}——开播后将自动录制。",
    "live.profile_watch_suffix": "（账号监控）",
    "live.paused_status": "已暂停",
    "live.rate_limited": "触发限流——{wait} 秒后重试",
    "live.fail_hint": "（失败 {failed}/{max}）",
    "live.recheck_in": "{remaining} 秒后重新检查{fail_hint}",
    "live.new_check_in": "{interval} 秒后进行新检查",
    "live.checking_stream": "正在检查直播…",
    "live.stream_live_starting": "直播进行中——正在启动录制…",
    "live.recording_dots": "录制中…",
    "live.cookie_old": "Cookie 已 {age} 天——可能出现认证错误",
    "live.cookie_banner": (
        "Cookie 文件已有 {age} 天——Instagram/TikTok 直播可能失败。请在 设置 → 网络 中刷新 Cookie。"
    ),
    "live.check_stuck_error": "检查卡住 {elapsed} 秒。请重试或检查网络连接。",
    "live.download_failed": "下载失败",
    "live.file_not_found": "未找到 .ts 文件。",
    "live.retry_failure_error": (
        "已重试 {failures} 次失败。最后错误：{err}\n"
        "请检查 Instagram Cookie 或网络连接，然后点击 x 重新添加网址。"
    ),
    "live.status_recording": "{count} 个录制中",
    "live.status_waiting": "{count} 个等待中",
    "live.status_ended": "{count} 个已完成",
    "convert.quality.high.label": "高质量",
    "convert.quality.high.desc": "H.264 CRF 18 · AAC 192k · 保留分辨率",
    "convert.quality.standard.label": "标准",
    "convert.quality.standard.desc": "H.264 CRF 23 · AAC 128k · 适配所有 iPhone",
    "convert.quality.small.label": "小文件",
    "convert.quality.small.desc": "H.264 CRF 28 · AAC 96k · 最高 720p",
    "convert.quality.custom.label": "自定义",
    "convert.quality.custom.desc": "自定义 CRF/CQ 值（16-35）",
    "convert.state.pending": "等待中",
    "convert.state.queued": "队列中",
    "convert.state.converting": "转换中…",
    "convert.state.done": "✓ 完成",
    "convert.state.failed": "✕ 错误",
    "convert.card.delete_output": "删除文件",
    "convert.card.output_gone": "→ {name}（已从磁盘删除）",
    "convert.card.before": "转换前",
    "convert.card.after": "转换后",
    "convert.card.reading": "读取中…",
    "convert.card.reading_info": "正在读取信息…",
    "convert.clear_done": "清除已完成/失败",
    "convert.select_all": "全选",
    "convert.select_all_tip": "选择/取消选择所有待处理文件。",
    "convert.select_file_tip": "将此文件加入转换。",
    "convert.parallel_label": "并行数：",
    "convert.parallel_tip": "同时转换的文件数（1-8）。数值越大越快，但占用更多 CPU。",
    "convert.settings_toggle_up": "⚙ 参数  ▲",
    "convert.settings_toggle_down": "⚙ 参数  ▼",
    "convert.quality_label": "画质",
    "convert.custom_value_label": "自定义数值（16-35）：",
    "convert.encoder_label": "编码器",
    "convert.speed_label": "速度",
    "convert.codec_label": "编码格式",
    "convert.subtitle_label": "字幕",
    "convert.gen_subs_check": "自动生成字幕 (.srt)",
    "convert.rating_label": "评分",
    "convert.vmaf_check": "与原始文件比较 VMAF 评分",
    "convert.vmaf_tip": (
        "使用 VMAF（0-100）比较转换后的视频与原始文件。\n"
        "分数越高越接近原片。评分需要重新解码两个文件，因此会增加耗时。"
    ),
    "convert.empty_title": "暂无文件",
    "convert.empty_hint": "点击“添加文件”或“添加文件夹”以选择视频",
    "convert.empty_formats": "支持：MP4、MKV、WebM、AVI、MOV、FLV、WMV、TS、3GP…",
    "convert.generate_subs_btn": "生成字幕",
    "convert.generate_subs_tip": "仅根据视频中的语音生成 .srt 文件，不重新编码视频。",
    "convert.convert_all_btn": "全部转换",
    "convert.no_whisper_tip": (
        "当前使用的 FFmpeg 版本没有 whisper 滤镜。\n需要“完整版” FFmpeg（见 README）才能自动生成字幕。"
    ),
    "convert.first_enable_tip": "首次启用会下载语音识别模型（约 141 MB），仅需下载一次。",
    "convert.gpu_status": "GPU：{labels}",
    "convert.cpu_only_status": "仅 CPU",
    "convert.choose_videos_title": "选择要转换的视频",
    "convert.choose_folder_title": "选择包含视频的文件夹",
    "convert.scanning_folder": "正在扫描文件夹…",
    "convert.no_video_in_folder": "在 {folder} 中未找到视频",
    "convert.added_from_folder": "已从 {folder} 添加 {count} 个文件",
    "convert.subtitle_created": "已生成字幕：{name}",
    "convert.status.processing": "处理中 {count}",
    "convert.status.waiting_count": "{count} 个等待",
    "convert.status.total_files": " / 共 {total} 个文件",
    "convert.status.complete": "完成 {done}/{total} 个文件 ✓",
    "convert.status.done_count": "{count} 个完成",
    "convert.status.failed_count": "{count} 个失败",
    "convert.delete_output_title": "删除已转换的文件",
    "convert.delete_output_msg": (
        "确定要删除电脑上已转换的文件吗？\n\n{name}\n\n删除前请确认文件已保存到 iPhone。\n此操作无法撤销。"
    ),
    "convert.delete_error_title": "删除出错",
    "convert.delete_error_msg": "无法删除文件：\n{err}",
    "convert.taildrop_sent": "已将 '{name}' 发送到 {node}",
    "convert.taildrop_failed": "Taildrop 失败 '{name}'：{err}",
    "settings.taildrop.description": (
        "下载完成后，通过 Taildrop（Tailscale）自动将文件发送到 iPhone。\n"
        "要求：本机安装 Tailscale CLI，且 iPhone 上已启用 Taildrop。\n"
        "文件会出现在 iOS 的“文件”应用中。"
    ),
    "settings.taildrop.enable": "启用 Taildrop",
    "settings.taildrop.cli_found": "✅  在 PATH 中检测到 tailscale CLI",
    "settings.taildrop.cli_not_found": "⚠️  未找到 tailscale CLI —— 请在本机安装 Tailscale",
    "settings.taildrop.mode_header": "📤  发送模式",
    "settings.taildrop.mode_auto_btn": "🔄  自动",
    "settings.taildrop.mode_manual_btn": "🖱️  手动",
    "settings.taildrop.mode_auto_desc": "⚡ 每次下载完成后会自动将文件发送到 iPhone。",
    "settings.taildrop.mode_manual_desc": "🖱️ 仅当你在远程界面手动点击“传输”时才会发送文件。",
    "settings.taildrop.nodes_header": "📱  目标设备（可选一个或多个）",
    "settings.taildrop.nodes_hint": "点击 🔍 查找设备 进行扫描，勾选要发送到的设备。",
    "settings.taildrop.no_nodes": "暂无设备。点击 🔍 进行扫描。",
    "settings.taildrop.manual_label": "或手动输入节点名称：",
    "settings.taildrop.manual_placeholder": "例如：iphone 或 100.64.x.x",
    "settings.taildrop.add_btn": "➕ 添加",
    "settings.taildrop.scan_btn": "🔍  查找 Tailscale 设备",
    "settings.taildrop.toggle_on": "📲  Taildrop 已启用。",
    "settings.taildrop.toggle_off": "📲  Taildrop 已禁用。",
    "settings.taildrop.mode_auto_label": "自动——每次下载完成后立即发送",
    "settings.taildrop.mode_manual_label": "手动——仅在你操作时发送",
    "settings.taildrop.mode_toast": "📤  Taildrop 模式：{label}。",
    "settings.taildrop.invalid_node": "❌  节点名称无效——只能包含字母、数字、连字符和点。",
    "settings.taildrop.node_added": "➕  已添加节点：{node}",
    "settings.taildrop.scanning": "⏳  正在扫描...",
    "settings.taildrop.no_devices_found": (
        "⚠️  未发现其他在线设备。\n→ 请在 iPhone 上打开 Tailscale 应用并确保已连接。"
    ),
    "settings.taildrop.devices_found": "✅  找到 {count} 台设备。请勾选要发送到的设备。",
    "settings.tools.section.ytdlp": "YT-DLP 引擎",
    "settings.tools.section.gallery_dl": "GALLERY-DL 引擎",
    "settings.tools.section.data_privacy": "数据与隐私",
    "settings.tools.version_label": "版本",
    "settings.tools.update_ytdlp_btn": "更新 yt-dlp",
    "settings.tools.checking_updates": "正在检查更新…",
    "settings.tools.updating_toast": "正在更新 {tool}，请稍候…",
    "settings.tools.updated_status": "已更新 → {ver}",
    "settings.tools.updated_toast": "{tool} 已更新到 {ver}",
    "settings.tools.update_error_status": "错误：{msg}",
    "settings.tools.update_failed_toast": "更新失败：{msg}",
    "settings.tools.keyring_btn": "安装 keyring（支持 Brave/Chrome 127+）",
    "settings.tools.keyring_not_installed": "⚠ 未安装——Brave/Chrome 127+ 需要",
    "settings.tools.keyring_warning": "⚠  如果 Brave/Chrome 在获取 Cookie 时报 DPAPI 错误，则需要此项。",
    "settings.tools.extra_args_label": "附加参数",
    "settings.tools.extra_args_placeholder": "例如：--no-playlist",
    "settings.tools.gallery_update_btn": "更新 gallery-dl",
    "settings.tools.clear_data_desc": (
        "清除所有应用数据：下载历史、配置设置\n"
        "以及 Cookie 文件路径。磁盘上的 Cookie 文件不会被删除。\n"
        "有下载/转换正在进行时无法执行此操作。"
    ),
    "settings.tools.clear_data_btn": "🗑  清除所有数据",
    "settings.tools.keyring_bundled_suffix": "（已内置）",
    "settings.tools.keyring_bundled_toast": "此版本已内置 keyring。请重试获取 Cookie。",
    "settings.tools.keyring_missing_status": "未找到 keyring——请下载更新版本",
    "settings.tools.keyring_missing_toast": "此版本中未找到 keyring。请下载最新的 EXE 版本。",
    "settings.tools.installing_keyring": "正在安装 keyring…",
    "settings.tools.keyring_installed_toast": "keyring 已安装。请重试从 Brave/Chrome 获取 Cookie。",
    "settings.tools.keyring_install_failed_toast": "安装 keyring 失败：{msg}",
    "settings.tools.clear_data_running": "在{parts}时无法清除数据。",
    "settings.tools.active_downloads": "{count} 个下载正在进行",
    "settings.tools.active_conversions": "{count} 个转换正在进行",
    "settings.tools.and_join": "和",
    "settings.tools.confirm_clear_title": "OmniDL — 确认清除数据",
    "settings.tools.confirm_clear_msg": (
        "此操作将：\n"
        "  • 清除全部下载历史\n"
        "  • 将所有设置重置为默认值\n"
        "  • 从配置中移除 Cookie 文件路径\n\n"
        "Cookie 文件和已下载的文件不会受到影响。\n\n是否继续？"
    ),
    "settings.tools.data_cleared_status": "✓ 已清除",
    "settings.tools.data_cleared_toast": "已清除所有数据。请重启应用以完全生效。",
    "settings.tools.clear_failed_toast": "清除数据失败：{msg}",
    "settings.api.section.remote": "远程 API",
    "settings.api.section.tailscale": "TAILSCALE HTTPS 配置",
    "settings.api.description": (
        "启用后可通过局域网（iPhone、Android）远程控制 OmniDL。\n"
        "服务器运行在独立线程中，不影响当前下载。\n"
        "仅在需要时启用——不使用时请关闭以保护设备安全。"
    ),
    "settings.api.enable": "启用远程 API",
    "settings.api.token_header": "🔑  Bearer Token",
    "settings.api.copy_btn": "📋 复制",
    "settings.api.rotate_btn": "🔄  生成新令牌",
    "settings.api.ts_description": (
        "通过 Tailscale 网络以 HTTPS 方式访问远程 API。\n"
        "OmniDL 会自动运行 tailscale serve——无需开放防火墙端口。\n"
        "要求：本机已安装并登录 Tailscale。"
    ),
    "settings.api.ts_enable": "启用 Tailscale HTTPS 配置",
    "settings.api.reset_profile_btn": "重置配置",
    "settings.api.no_token": "（尚无令牌——启用 API 后自动生成）",
    "settings.api.running": "🟢  运行中  —  http://<局域网 IP>:{port}",
    "settings.api.enabled_not_started": "⚠️  已启用但尚未启动（缺少 fastapi/uvicorn？）",
    "settings.api.disabled": "⚫  已禁用",
    "settings.api.enabled_toast": "✅  远程 API 已启用。",
    "settings.api.missing_deps": "⚠️  需先安装 fastapi 和 uvicorn：pip install -r requirements-api.txt",
    "settings.api.start_error": "启动 API 出错：{err}",
    "settings.api.disabled_toast": "⚫  远程 API 已禁用。",
    "settings.api.no_token_toast": "尚无令牌。请先启用远程 API。",
    "settings.api.token_copied": "✅  令牌已复制到剪贴板。",
    "settings.api.rotate_dialog_title": "生成新令牌",
    "settings.api.rotate_confirm_msg": "当前令牌将失效。\n所有已连接设备都需要更新为新令牌。\n\n是否继续？",
    "settings.api.new_token_saved": "✅  新令牌已保存",
    "settings.api.restarting_toast": "🔄  新令牌已生成——正在重启服务器…",
    "settings.api.restarted_toast": "✅  服务器已使用新令牌重启。",
    "settings.api.restart_error": "重启 API 出错：{err}",
    "settings.api.new_token_toast": "✅  新令牌已生成。请复制并更新到设备上。",
    "settings.api.ts_running": "远程 API 正在运行：\nhttps://{dns}",
    "settings.api.ts_internal_port": "内部端口（随机）：{port}",
    "settings.api.ts_setting_up": "正在设置...（检查 Tailscale 是否已连接）",
    "settings.api.ts_internal_port_plain": "内部端口：{port}",
    "settings.api.ts_off": "已禁用",
    "settings.api.enable_api_first": "请先启用远程 API，再使用 Tailscale HTTPS 配置。",
    "settings.api.no_tailscale_cli": "未找到 tailscale CLI——请在本机安装 Tailscale。",
    "settings.api.setting_up_https": "正在设置 Tailscale HTTPS 配置...",
    "settings.api.login_hint": " Tailscale 尚未登录？运行 'tailscale login' 后重试。",
    "settings.api.serve_failed": "tailscale serve 失败。{hint}",
    "settings.api.https_enabled_toast": "HTTPS 配置已启用：https://{dns}",
    "settings.api.no_dns_error": "serve 已启动但未能获取 DNS 名称。请检查 Tailscale 是否已登录。",
    "settings.api.setup_error": "设置 HTTPS 配置出错：{err}",
    "settings.api.disabling_https": "正在禁用 Tailscale HTTPS 配置...",
    "settings.api.https_disabled_toast": "Tailscale HTTPS 配置已禁用。",
    "settings.api.disable_error": "禁用 HTTPS 配置出错：{err}",
    "settings.api.enable_https_first": "重置前请先启用 HTTPS 配置。",
    "settings.api.reset_confirm_msg": (
        "此操作将：\n• 重新创建 Tailscale serve 配置\n• 生成新令牌（设备需更新）\n\n是否继续？"
    ),
    "settings.api.resetting_status": "正在重置...",
    "settings.api.resetting_toast": "正在重置 Tailscale HTTPS 配置...",
    "settings.api.reset_serve_failed": "重置时 tailscale serve 失败。请检查 Tailscale 是否已登录。",
    "settings.api.reset_success_dns": "配置已重置：https://{dns}。新令牌已生成——请更新设备。",
    "settings.api.reset_success": "配置已重置。新令牌已生成——请更新设备。",
    "settings.api.reset_error": "重置配置出错：{err}",
    "settings.network.section.auth": "网络与身份验证",
    "settings.network.proxy_label": "Proxy URL",
    "settings.network.browser_label": "来源浏览器",
    "settings.network.use_cookies_label": "使用 Cookie",
    "settings.network.section.auto_extract": "自动获取 COOKIE",
    "settings.network.extract_global_btn": "🔄  Firefox / Edge / Opera",
    "settings.network.extract_cdp_btn": "🦁  Brave / Chrome 127+",
    "settings.network.extract_hint": (
        "🔄 = yt-dlp 直接读取（需先关闭 Brave/Chrome）   •   🦁 = CDP——无需关闭浏览器，Brave 127+ 安全可用"
    ),
    "settings.network.section.manual_import": "手动导入文件",
    "settings.network.fallback_hint": (
        "🌐  Cookie 兜底——YouTube、Twitch、Vimeo 等\n（当平台尚未出现在下方“分平台”表格中时使用）"
    ),
    "settings.network.fallback_warning": (
        "⚠  此文件包含浏览器的全部 Cookie（Google、邮箱、网银等）。\n"
        "   建议优先使用下方“分平台”表格，更安全。"
    ),
    "settings.network.browse_btn": "浏览…",
    "settings.network.clear_btn": "🗑 清除",
    "settings.network.section.per_platform": "分平台 COOKIE",
    "settings.network.recommended_badge": "推荐",
    "settings.network.per_platform_desc": (
        "✅ 建议优先使用此表——每个文件只包含对应平台的 Cookie。\n"
        "TikTok 文件不含 Google/邮箱 Cookie，Instagram 文件不含网银 Cookie。\n"
        "如果平台在此有单独一行 → 就不需要使用上方的 Cookie 兜底了。"
    ),
    "settings.network.cdp_btn": "CDP",
    "settings.network.cdp_tip": "CDP——Brave/Chrome 127+（无需关闭浏览器）",
    "settings.network.ytdlp_btn": "yt-dlp",
    "settings.network.ytdlp_tip": "yt-dlp——Firefox / Edge / Opera（需先关闭 Brave/Chrome）",
    "settings.network.choose_btn": "选择",
    "settings.network.choose_tip": "手动选择 Cookie 文件（.txt Netscape 格式）",
    "settings.network.delete_tip": "删除该平台的 Cookie 文件",
    "settings.network.extract_footer_hint": (
        "🔄 = yt-dlp（Firefox/Opera）。 🦁 = CDP（Brave/Chrome 127+，无需关闭浏览器）。"
    ),
    "settings.network.section.tiktok_accounts": "TIKTOK 账号",
    "settings.network.pool_badge": "账号池",
    "settings.network.tiktok_desc": (
        "每个账号最多分配 N 个并发下载槽位。\n账号池为空时，应用会使用上方的“分平台 TikTok Cookie”。"
    ),
    "settings.network.no_accounts": "暂无账号。点击“+ 添加账号”添加。",
    "settings.network.add_account_btn": "+ 添加账号",
    "settings.network.name_label": "名称：",
    "settings.network.name_placeholder": "账号 1",
    "settings.network.no_cookie_chosen": "尚未选择 Cookie",
    "settings.network.save_btn": "保存",
    "settings.network.slots_tip": "该账号最多同时下载数",
    "settings.network.resume_btn": "恢复",
    "settings.network.pause_btn": "暂停",
    "settings.network.default_account_name": "账号",
    "settings.network.select_tiktok_cookie_title": "选择 TikTok Cookie 文件（Netscape 格式）",
    "settings.network.not_netscape_format": "文件不是 Netscape Cookie 格式。",
    "settings.network.copy_failed": "无法复制文件：{err}",
    "settings.network.cdp_unsupported_browser": "CDP 仅支持 Brave/Chrome/Edge。",
    "settings.network.starting_browser": "正在启动 {browser}...",
    "settings.network.cdp_failed": "CDP 失败：{err}",
    "settings.network.cdp_got_tiktok": "CDP：已获取 {count} 个 TikTok Cookie。",
    "settings.network.reading_tiktok_from": "正在从 {browser} 读取 TikTok Cookie...",
    "settings.network.extract_failed": "失败：{err}",
    "settings.network.got_tiktok_cookies": "已获取 {count} 个 TikTok Cookie。",
    "settings.network.account_added": "已添加账号 '{name}'。",
    "settings.network.profile_label": "配置文件：",
    "settings.network.profile_default": "默认",
    "settings.network.profile_tip": (
        "每个浏览器配置文件都保存各自的登录会话。\n将每个 TikTok 账号登录到不同的配置文件，然后在此逐个添加。"
    ),
    "settings.network.slots_label": "槽位：",
    "settings.network.source_manual": "手动文件",
    "settings.network.cookie_ready": "已就绪——{count} 个 Cookie，已登录。",
    "settings.network.reject_missing": "找不到刚提取的 Cookie 文件。",
    "settings.network.reject_unreadable": "无法读取 Cookie 文件。",
    "settings.network.reject_not_logged_in": (
        "该配置文件尚未登录 TikTok。请先在该配置文件登录，然后重新提取。"
    ),
    "settings.network.reject_expired": "登录会话已过期。请在浏览器中重新登录，然后重新提取。",
    "settings.network.duplicate_account": (
        "该 TikTok 账号已在账号池中（'{name}'）。请选择其他浏览器配置文件。"
    ),
    "settings.network.name_in_use": "名称 '{name}' 已被使用。",
    "settings.network.rename_tip": "点击以重命名该账号",
    "settings.network.refresh_btn": "↻",
    "settings.network.refresh_tip": "重新提取该账号的 Cookie（使用已保存的浏览器/配置文件）",
    "settings.network.refresh_no_source": "该账号是通过手动文件添加的——请删除后重新添加新文件。",
    "settings.network.refreshing": "正在刷新 '{name}' 的 Cookie...",
    "settings.network.account_refreshed": "已刷新 '{name}' 的 Cookie。",
    "settings.network.status_ok": "正常——约剩 {days} 天",
    "settings.network.status_ok_session": "正常",
    "settings.network.status_paused": "已暂停",
    "settings.network.status_missing": "缺少 Cookie 文件",
    "settings.network.status_unreadable": "无法读取 Cookie 文件",
    "settings.network.status_not_logged_in": "Cookie 未登录 TikTok",
    "settings.network.status_expired": "Cookie 已过期——点击 ↻ 重新提取",
    "settings.network.pool_hint": (
        "提示：一个 TikTok 账号对应一个浏览器配置文件。"
        "在 Brave/Chrome/Edge 中新建配置文件，在其中登录第二个账号，"
        "然后回到这里选择该配置文件——无需退出任何账号。"
    ),
    "settings.network.account_rejected": "无法添加账号 —— cookie 文件必须位于应用的 cookies 目录内。",
    "settings.network.proxy_invalid": ("Proxy 无效——必须以 http://、https://、socks4:// 或 socks5:// 开头"),
    "settings.network.select_cookies_title": "选择 cookies.txt（Netscape 格式）",
    "settings.network.file_not_found": "未找到文件。",
    "settings.network.not_netscape_full": (
        "文件不是 Netscape Cookie 格式。\n请选择从浏览器或 Cookie-Editor 扩展导出的 cookies.txt 文件。"
    ),
    "settings.network.copy_failed_full": "无法复制 Cookie 文件：{err}",
    "settings.network.cookie_saved_encrypted": "Cookie 文件已加密并保存到安全文件夹。",
    "settings.network.no_file_selected": "未选择文件",
    "settings.network.cdp_confirm_title": "OmniDL — 确认获取全部 Cookie（CDP）",
    "settings.network.cdp_confirm_msg": (
        "⚠ 此操作会获取 Brave/Chrome 的全部 Cookie，\n"
        "包括 Google、邮箱、网银等...\n\n"
        "Cookie 会通过 DPAPI 加密并仅保存在本机。\n"
        "本地会临时开放一个随机端口，持续约 10 秒。\n\n"
        "➡ 建议：改用下方各平台的 🦁 按钮，\n"
        "   只获取所需的 Cookie（更安全）。\n\n"
        "是否继续获取全部？"
    ),
    "settings.network.cdp_browser_unsupported": (
        "CDP 仅支持 Brave/Chrome/Edge。当前浏览器：{browser}。\n请为 Firefox/Opera/Safari 使用 🔄 按钮。"
    ),
    "settings.network.error_status": "❌ {err}",
    "settings.network.cdp_global_failed_toast": "CDP 失败：{err}",
    "settings.network.cdp_saved_status": "✓ 已保存 {count} 个 Cookie（CDP）",
    "settings.network.cdp_from_browser_toast": "CDP：已从 {browser} 获取 {count} 个 Cookie。",
    "settings.network.starting_cdp_status": "正在启动 {browser}（CDP）…",
    "settings.network.global_confirm_title": "OmniDL — 确认获取全部 Cookie",
    "settings.network.global_confirm_msg": (
        "⚠ 此操作会获取浏览器的全部 Cookie，\n"
        "包括 Google、邮箱、网银等...\n\n"
        "Cookie 会通过 DPAPI 加密并仅保存在本机。\n\n"
        "➡ 建议：改用下方各平台的 🔄 / 🦁 按钮，\n"
        "   只获取所需的 Cookie（更安全）。\n\n"
        "是否继续获取全部？"
    ),
    "settings.network.extract_failed_toast": "获取 Cookie 失败：{err}",
    "settings.network.saved_status": "✓ 已保存 {count} 个 Cookie{note}",
    "settings.network.encrypted_note": " 🔒（DPAPI 加密）",
    "settings.network.got_from_browser_toast": "已从 {browser} 获取 {count} 个 Cookie{note}。",
    "settings.network.reading_from_browser_status": "正在从 {browser} 读取 Cookie…",
    "settings.network.select_cookie_for": "为 {platform} 选择 Cookie 文件（Netscape 格式）",
    "settings.network.file_not_found_vi": "未找到文件。",
    "settings.network.copy_platform_failed": "无法复制 Cookie 文件：{err}",
    "settings.network.platform_cookie_saved": "{platform} Cookie 已加密并保存到安全文件夹。",
    "settings.network.platform_extract_failed_status": "❌ {platform}：{err}",
    "settings.network.platform_extract_failed_toast": "获取 {platform} Cookie 失败：{err}",
    "settings.network.platform_saved_status": "✓ {platform}：已保存 {count} 个 Cookie",
    "settings.network.platform_got_toast": "已从 {browser} 获取 {count} 个 {platform} Cookie。",
    "settings.network.reading_platform_status": "正在从 {browser} 读取 {platform} Cookie…",
    "settings.network.cdp_unsupported_use_browser": "CDP 仅支持 Brave/Chrome/Edge。请为 {browser} 使用 🔄。",
    "settings.network.platform_cdp_failed_status": "❌ {platform} CDP：{err}",
    "settings.network.platform_cdp_failed_toast": "CDP {platform} 失败：{err}",
    "settings.network.platform_cdp_saved_status": "✓ {platform}：{count} 个 Cookie（CDP）",
    "settings.network.platform_cdp_toast": "CDP：已获取 {count} 个 {platform} Cookie。",
    "settings.network.starting_cdp_platform_status": "正在启动 {browser} 以获取 {platform} Cookie…",
    # ── Added by the tab audit (home / cards / status bar) ───────────────
    "home.welcome_title": "准备下载",
    "home.welcome_sub": "在上方栏中粘贴视频链接并点击分析",
    "home.more_platforms": "+ 1000 个平台",
    "home.loading": "正在加载媒体信息…",
    "home.loading_sub": "可能需要几秒钟",
    "home.quality_section": "画质",
    "home.format_label": "格式",
    "home.folder_label": "文件夹",
    "home.browse": "浏览",
    "home.add_to_queue": "加入队列",
    "home.no_preview": "无预览",
    "home.choose_dir": "选择下载文件夹",
    "home.photo_note": "图片 — 以最高可用分辨率下载",
    "home.no_media_info": "未返回媒体信息。",
    "home.batch_unavailable": "无法打开批量下载标签页。",
    "home.display_error": "显示错误：{err}",
    "home.added_toast": "已添加：{title}",
    "home.quality.best": "最高画质",
    "home.quality.4k": "4K / 2160p",
    "home.quality.1080": "1080p 全高清",
    "home.quality.720": "720p 高清",
    "home.quality.480": "480p",
    "home.quality.360": "360p",
    "home.quality.audio_mp3": "仅音频 MP3",
    "home.quality.audio_m4a": "仅音频 M4A",
    "item.status.queued": "等待中",
    "item.status.downloading": "下载中",
    "item.status.processing": "处理中",
    "item.status.paused": "已暂停",
    "item.status.completed": "已完成",
    "item.status.failed": "失败",
    "item.status.cancelled": "已取消",
    "item.status.partial": "已保存部分",
    "item.status.unknown": "未知",
    "item.pause_tip": "暂停 / 继续",
    "item.cancel_tip": "取消下载",
    "item.open": "打开",
    "item.open_tip": "打开所在文件夹",
    "item.preview": "查看",
    "item.preview_tip": "查看 / 播放文件",
    "item.convert": "转换",
    "item.convert_tip": "转换格式",
    "item.edit": "编辑",
    "item.edit_tip": "剪辑 / 编辑视频",
    "item.send": "发送",
    "item.send_tip": "通过 Taildrop 发送文件",
    "item.sending": "发送中…",
    "item.rename": "重命名",
    "item.rename_tip": "重命名文件",
    "pda.convert": "转换",
    "pda.convert_compact": "转换",
    "pda.converting": "正在转换 → .{ext}…",
    "pda.converting_short": "转换中…",
    "pda.done": "完成",
    "pda.send": "发送",
    "pda.sending": "发送中…",
    "pda.delete": "删除",
    "pda.edit": "编辑",
    "pda.convert_failed": "转换失败：{msg}",
    "pda.convert_error_file": "{name} 转换错误：{err}",
    "pda.convert_error": "转换错误：{err}",
    "pda.empty_folder": "文件夹为空。",
    "pda.pick_send_title": "选择要发送的文件",
    "pda.pick_send_confirm": "发送所选",
    "pda.pick_delete_title": "选择要删除的文件",
    "pda.pick_delete_confirm": "删除所选",
    "pda.confirm_delete_title": "确认删除",
    "pda.confirm_delete_all": "删除此帖子的全部 {count} 个文件？",
    "pda.confirm_delete_empty_dir": "删除空文件夹「{name}」？",
    "pda.confirm_delete_file": "确定要删除此文件吗？\n{name}",
    "pda.delete_failed": "无法删除：{err}",
    "pda.target_format": "选择目标格式：",
    "pda.fmt.custom": "自定义（画质 + 编码器）…",
    "pda.encoder": "编码器：",
    "pda.quality": "画质：",
    "pda.crf": "CRF：",
    "pda.speed": "速度：",
    "pda.q.high": "高（CRF 18）",
    "pda.q.standard": "标准（CRF 23）",
    "pda.q.small": "小体积 720p（CRF 28）",
    "pda.q.custom": "自定义 CRF",
    "pda.pick_files_label": "选择要处理的文件：",
    "pda.select_all": "全选",
    "pda.deselect_all": "取消全选",
    "status.idle": "无进行中的任务",
    "status.downloading": "{count} 个下载中",
    "status.net_ok": "网络正常",
    "status.net_down": "连接中断",
    "status.eta_min": "剩余约 {value} 分钟",
    "status.eta_sec": "剩余约 {value} 秒",
    "palette.search": "搜索命令…",
    "app.quit.downloads": "{count} 个下载",
    "app.quit.conversions": "{count} 个转换",
    "app.quit.and": " 和 ",
    "app.quit.confirm": "{summary} 仍在运行。\n关闭并取消全部？",
    "settings.clipboard_on": "已开启剪贴板监听",
    "settings.clipboard_off": "已关闭剪贴板监听",
    "settings.debug_on": "已开启调试日志 — 写入 omnidl_debug.log",
    "settings.debug_off": "已关闭调试日志",
    "convert.cancelled": "已取消",
    "pda.fmt.mp4": "MP4 — H.264 / AAC (iPhone, Android)",
    "pda.fmt.mp3": "MP3 — 仅音频",
    "pda.fmt.mkv": "MKV — 无损容器",
    "pda.fmt.avi": "AVI — 兼容旧设备",
    "convert.codec.h264": "H.264（兼容性最好）",
    "convert.codec.hevc": "H.265 / HEVC（约小 30%）",
    "convert.codec.av1": "AV1（约小 50%，需 ff8+）",
    "convert.speed.quality": "慢速（压缩最佳）",
    "convert.speed.balanced": "均衡",
    "convert.speed.fast": "快速（压缩较少）",
    "convert.err.file_missing": "文件不存在：{path}",
    "convert.err.ffmpeg_exit": "ffmpeg 以错误 {code} 退出。\n{tail}",
    "trim.err.no_ffmpeg": "未找到 FFmpeg。请安装 FFmpeg 后重试。",
    "subs.model.tiny": "Tiny（74 MB，最快）",
    "subs.model.base": "Base（141 MB，均衡）",
    "subs.model.small": "Small（465 MB，更准确）",
    "subs.model.medium": "Medium（1.4 GB，最佳）",
    "subs.lang.auto": "自动检测",
    "subs.err.invalid_model": "无效的模型：{model}",
    "subs.err.invalid_language": "无效的语言：{language}",
    "subs.err.no_whisper": "当前 FFmpeg 版本不含 whisper 滤镜 — 需要 full 版本（见 README）。",
    "subs.err.incomplete_download": "模型下载不完整（{got}/{total} 字节）",
    "subs.err.failed": "字幕生成失败：{err}",
    "subs.err.no_speech": "未在视频中检测到语音。",
    # ── Engine / service messages ─────────────────────────────────────────
    # Errors and progress text raised outside the UI layer (download engines,
    # cookie extraction, live checkers).  Retry logic keys off the catalogue
    # KEY, never this text — see yt_dlp_engine._error_key().
    "err.private": "内容为私密。请在设置中启用 Cookie。",
    "err.not_found": "找不到该 URL，或内容已被删除。",
    "err.unsupported_platform": "yt-dlp 不支持该平台。",
    "err.live_not_started": "直播尚未开始。",
    "err.not_currently_live": "该频道当前未在直播。",
    "err.live_ended": "直播已结束。",
    "err.ig_photo_only": (
        "此帖子只有图片，没有视频。\n"
        "OmniDL 将以 format='best' 重试下载图片。\n"
        "若仍失败，请确认使用的是 Instagram Cookie（不是 Facebook）且 yt-dlp 为最新版本。"
    ),
    "err.ytdlp_internal": (
        "yt-dlp 解析此 URL 时发生内部错误。\n"
        "请将 yt-dlp 更新到最新版本：\n"
        "设置 → 更新 yt-dlp，或运行：pip install -U yt-dlp"
    ),
    "err.ig_checkpoint": (
        "Instagram 要求验证账号。\n"
        "1. 在浏览器中打开 Instagram 并完成验证。\n"
        "2. 重新导出 Cookie（可用 'Get cookies.txt LOCALLY' 扩展）。\n"
        "3. 在 设置 → 网络 → Cookie 文件 中更新该文件。\n"
        "注意：Instagram Cookie 通常 1–2 周后过期。"
    ),
    "err.rate_limit": (
        "已达到频率限制——短时间内请求过多。\n"
        "请等待 5–10 分钟后重试。在设置中启用浏览器 Cookie 可能有帮助。"
    ),
    "err.tls_fingerprint": (
        "TLS 连接错误——服务器拒绝了默认的 TLS 指纹。\n"
        "OmniDL 使用 curl_cffi（模拟 Chrome）来规避此问题。\n"
        "若错误仍然出现：\n"
        "  1. 检查杀毒软件/代理是否拦截 HTTPS\n"
        "  2. 在 设置 → 网络 → Proxy URL 中尝试使用代理\n"
        "  3. 运行：pip install -U curl-cffi"
    ),
    "err.fb_unavailable": "该 Facebook 内容不可用。可能需要登录，或仅限特定地区访问。",
    "err.geo_restricted": (
        "该内容有地区限制，在你所在区域不可用。\n"
        "可在 设置 → 网络 → Proxy URL 中启用 VPN 或代理。"
    ),
    "err.ffmpeg_livestream": (
        "无法录制直播——ffmpeg 报错。\n"
        "常见原因：\n"
        "  • 直播链接已过期（TikTok 链接约 1–2 分钟后失效）\n"
        "    → 请重新复制链接并立即开始下载\n"
        "  • 直播已结束或已暂停\n"
        "  • 录制过程中网络中断\n"
        "若错误仍然出现：请重新复制链接，或等待直播稳定后重试。"
    ),
    "err.ip_blocked": (
        "你的 IP 被 TikTok/该平台阻止访问此帖子。\n"
        "常见原因：\n"
        "  • 请求过多导致 IP 被临时拉黑（临时频率限制）\n"
        "  • ISP/VPS/机房 IP 因地区策略被封锁\n"
        "解决办法：\n"
        "  1. 在 设置 → 网络 → Proxy URL 中启用代理/VPN\n"
        "     （例如本地代理 socks5://127.0.0.1:1080）\n"
        "  2. 等待 5–15 分钟后重试（若为临时频率限制）\n"
        "  3. 刷新 TikTok Cookie：设置 → 各平台 Cookie → TikTok"
    ),
    "err.tiktok_login_required": (
        "TikTok 要求登录后才能下载此视频。\n"
        "Cookie 池可能已过期，或属于其他账号。\n"
        "解决办法：在 设置 → 各平台 Cookie → TikTok 中刷新 TikTok Cookie"
    ),
    "err.copyright": "该内容因版权投诉被屏蔽。",
    "err.blocked": "该内容被屏蔽或访问被拒绝。\n可在 设置 → 网络 → Proxy URL 中启用 VPN 或代理。",
    "err.account_suspended": "发布该内容的账号已被封禁。",
    "err.members_only": "该内容仅面向会员/订阅者。\n请确认已在设置中通过 Cookie 登录。",
    "err.tiktok_10231": (
        "TikTok API 拒绝了该请求（状态 10231），尽管视频仍可观看。\n"
        "可尝试：\n"
        "  1. 刷新 TikTok Cookie：设置 → 各平台 Cookie → TikTok\n"
        "  2. 在 设置 → 网络 → Proxy URL 中启用代理/VPN"
    ),
    "err.video_deleted": (
        "该视频已不存在或已被删除。\n"
        "请检查 URL——若为短链接（vt.tiktok.com），可在浏览器中打开以获取完整链接。"
    ),
    "err.threads_unsupported": (
        "yt-dlp 暂不支持 Threads 帖子。\n"
        "\n"
        "保存 Threads 视频的方法：\n"
        "• 在浏览器中打开帖子 → 点击 ... → 保存\n"
        "• 或使用浏览器的 “Video Downloader” 扩展。"
    ),
    "err.ig_stories_cookies": "Instagram Stories 需要登录 Cookie。\n请在 设置 → 网络 → Cookie 文件 中配置。",
    "err.ig_live_cookies": "Instagram Live 需要登录 Cookie。\n请在 设置 → 网络 → Cookie 文件 中配置。",
    "err.fb_live_cookies": "Facebook Live 需要 Cookie。\n请在 设置 → 网络 → Cookie 文件 中配置。",
    "err.fb_stories_manual": (
        "Facebook Stories 无法自动下载。\n"
        "\n"
        "保存 Facebook Story 的方法：\n"
        "• 在浏览器中打开 Story → 点击 ... → 保存视频\n"
        "• 或使用浏览器的 “Video Downloader” 扩展。"
    ),
    "err.ig_cookie_expired": "Instagram Cookie 已过期——请在设置中刷新 Cookie。",
    "err.playlist_failed": "无法从该 URL 读取列表：{err}",
    "err.no_data": "该 URL 未返回数据。请检查 URL，或在设置中添加 Cookie 文件。",
    "err.playlist_empty": "该播放列表/主页没有可用视频。\n账号可能为私密，或需要 Cookie 文件。",
    "err.ffmpeg_not_found": "找不到 FFmpeg。请检查 FFmpeg 安装。",
    "err.ffmpeg_stall": "FFmpeg 停滞监控：120 秒无数据——直播可能已结束。",
    "err.no_error_detail": "没有可用的错误信息。",
    "err.hls_stall": "停滞监控：curl_cffi HLS 已 {seconds} 秒未收到新分片——直播可能已结束。",
    "err.livestream_ended_relink": "直播已结束，或 HLS 链接已失效。\n可重新添加链接以监控下一次直播。",
    "err.tiktok_audio_only": (
        "该内容只有音频——没有视频轨。\n"
        "TikTok 商品/橱窗以及 “模板特效” / AR 特效视频不通过 API 提供视频轨（仅提供音频流）。\n"
        "保存方法：在 TikTok App 中打开视频 → 分享 → 保存视频。"
    ),
    "err.tiktok_ec_blocked": (
        "该视频无法下载——TikTok 完全屏蔽了视频 URL。\n"
        "这是电商/商品视频（isECVideo=1）：TikTok 不向任何 API 客户端提供视频 URL。\n"
        "保存方法：在 TikTok App 中打开视频 → 分享 → 保存视频。"
    ),
    "err.no_username_from_url": "无法从该 URL 中解析出用户名。",
    "err.no_username_from_tiktok_url": "无法从该 TikTok URL 中解析出用户名。",
    "err.invalid_url_scheme": "URL 无效——必须以 http:// 或 https:// 开头",
    "err.monitor_limit": "已达到 {count} 个 URL 的上限。",
    "err.already_monitored": "该 URL 已在监控中。",
    "err.profile_watch_needs_ig_cookie": (
        "主页监控需要 Instagram Cookie 文件。请在 设置 → 网络 → Cookie 文件 中配置。"
    ),
    "err.check_stuck": "检查过程卡住。请重试，或检查网络连接。",
    "err.attempts_failed": "已失败 {count} 次。最后一次错误：{err}",
    "err.download_failed": "下载失败",
    "err.network": "网络错误：{err}",
    "err.http": "HTTP 错误：{err}",
    "err.cookie_unreadable": "无法读取 Cookie 文件：{err}",
    "err.ig_cookie_required": (
        "检查直播状态需要 Instagram Cookie 文件。\n"
        "请在 设置 → 网络 → Cookie 文件 中配置。"
    ),
    "err.ig_cookie_no_sessionid": (
        "Cookie 文件中没有 Instagram 的 sessionid。\n"
        "请登录 Instagram 后重新导出 Cookie 文件。"
    ),
    "err.ig_cookie_invalid": "Instagram Cookie 已过期或无效。\n请在 设置 → 网络 中刷新 Cookie 文件。",
    "err.ig_account_not_found": "找不到账号 @{username}。",
    "err.ig_rate_limited": "触发频率限制——Instagram 暂时拦截。\n请等待 5–10 分钟后重试。",
    "err.ig_forbidden": "访问被拒绝（403）。Cookie 可能已过期。",
    "err.ig_api_timeout": "Instagram API 超时。请稍后重试。",
    "err.ig_bad_response": "Instagram 返回了无效响应。请稍后重试，或检查 Cookie 文件。",
    "err.tiktok_api_timeout": "TikTok API 超时。请稍后重试。",
    "err.no_username_from_facebook_url": "无法从该 Facebook URL 中解析出用户名。",
    "err.profile_watch_needs_fb_cookie": (
        "监控 Facebook 主页需要 Facebook Cookie 文件。请在 设置 → 网络 → Cookie 文件 中配置。"
    ),
    "err.fb_cookie_required": (
        "检查直播状态需要 Facebook Cookie 文件。\n"
        "请在 设置 → 网络 → Cookie 文件 中配置。"
    ),
    "err.fb_cookie_no_session": (
        "Cookie 文件中没有 Facebook 登录会话（缺少 c_user/xs）。\n"
        "请登录 Facebook 后重新导出 Cookie 文件。"
    ),
    "err.fb_cookie_invalid": "Facebook Cookie 已过期或无效。\n请在 设置 → 网络 中刷新 Cookie 文件。",
    "err.fb_page_not_found": "找不到 Facebook 主页 {username}。",
    "err.fb_rate_limited": "触发频率限制——Facebook 暂时拦截。\n请等待 5–10 分钟后重试。",
    "err.fb_api_timeout": "Facebook 超时。请稍后重试。",
    "progress.recorded": "⏺ 已录制 {size}",
    "cookie.err.app_bound_encryption": (
        "Brave/Chrome 127+ 使用 App-Bound Encryption——无法从浏览器外部读取 Cookie。\n"
        "这是 Windows/Chrome 的安全限制，并非程序错误。\n"
        "\n"
        "✅ 最快的方法：使用 Firefox\n"
        "   1. 打开 Firefox 并登录 TikTok/Instagram/...\n"
        "   2. 将下拉框切换到 firefox → 点击 🔄\n"
        "\n"
        "📁 或从 Brave 手动导出：\n"
        "   安装 Cookie-Editor 扩展 → 导出 → Netscape 格式\n"
        "   → 设置 → 浏览… → 选择导出的 .txt 文件"
    ),
    "cookie.err.brave_locked": (
        "Brave 正在运行——Cookie 数据库被锁定。\n"
        "⚠ 请完全关闭 Brave（包括系统托盘中的后台进程），\n"
        "然后再次点击 🔄。提取完成后即可重新打开 Brave。"
    ),
    "cookie.err.db_missing_detected": (
        "找不到 “{browser}” 的 Cookie 数据库。\n"
        "⚠ 请在 “Cookie source browser” 下拉框中选择你实际使用的浏览器，\n"
        "然后再次点击 🔄。"
    ),
    "cookie.err.db_missing": (
        "找不到所选浏览器的 Cookie 数据库。\n"
        "⚠ 请在 “Cookie source browser” 下拉框中选择你实际使用的浏览器，\n"
        "然后再次点击 🔄。"
    ),
    "cookie.err.db_locked": (
        "无法读取 Cookie——浏览器正在运行并锁定了数据库。\n"
        "请完全关闭浏览器（包括后台进程）后重试。"
    ),
    "cookie.err.decrypt_failed": "无法解密浏览器 Cookie。\n请尝试以管理员身份运行 OmniDL，或改选其他浏览器。",
    "cookie.err.profile_missing": (
        "找不到所选浏览器的配置文件。\n"
        "⚠ 请重新检查 “Cookie source browser” 下拉框——选择你实际使用的浏览器\n"
        "（例如 Brave ≠ Chrome）。"
    ),
    "cookie.err.permission_denied": "访问浏览器 Cookie 文件被拒绝。\n请尝试以管理员身份运行 OmniDL。",
    "cookie.err.browser_unsupported": "yt-dlp 不支持该浏览器。\n请尝试 Chrome、Firefox 或 Edge。",
    "cookie.err.cdp_no_connection": "无法连接到浏览器。\n请重试——首次启动可能需要几秒钟。",
    "cookie.err.cdp_websocket": "与浏览器的 WebSocket 连接失败。\n请重试，或重启 OmniDL。",
    "cookie.err.cdp_no_cookies": "浏览器未返回任何 Cookie。请先登录相关网站后再试。",
    "cookie.err.read_failed": "无法读取 Cookie——浏览器可能未登录，或数据库被锁定。请尝试完全关闭浏览器。",
    "cookie.err.browser_empty": "该浏览器中没有任何 Cookie。请先登录你要下载的网站。",
    "cookie.err.platform_empty": (
        "在浏览器中找不到 {platform} 的 Cookie。\n"
        "请确认已在该浏览器中登录 {platform}。"
    ),
    "cookie.err.platform_empty_browser": (
        "在 {browser} 中找不到 {platform} 的 Cookie。\n"
        "请确认已在 {browser} 中登录 {platform}。"
    ),
    "cookie.err.cdp_windows_only": (
        "CDP 模式（🦁）目前仅支持 Windows。\n"
        "在 macOS/Linux 上请使用 🔄 按钮（yt-dlp），或手动选择 Cookie 文件。"
    ),
    "cookie.err.browser_not_installed": "在本机上找不到 {browser}。\n请检查是否已安装 Brave/Chrome。",
    "cookie.err.cdp_zero_cookies": "浏览器返回了 0 个 Cookie。\n请先登录相关网站再提取 Cookie。",
    "cookie.err.cdp_timeout": "20 秒内无法连接到 {browser}。\n请重试——首次启动可能需要更久。",
    "cookie.err.browser_running": (
        "{browser} 正在运行——需要暂时关闭才能读取 Cookie。\n"
        "\n"
        "⚠ 请完全关闭 {browser}（包括系统托盘），\n"
        "然后再次点击 🦁。提取完成后即可重新打开。\n"
        "\n"
        "原因：CDP 需要读取真实的配置文件，而该文件当前被 {browser} 锁定。"
    ),
    "cookie.err.profile_dir_missing": (
        "找不到 {browser} 的配置文件目录。\n"
        "请检查 Brave/Chrome 是否已安装并至少登录过一次。"
    ),
    "err.gdl_login_required": (
        "gallery-dl 需要登录。\n"
        "请检查 设置 → 网络 → Cookie 文件。\n"
        "请确认使用的是 Instagram Cookie（不是 Facebook）。"
    ),
    "err.gdl_rate_limited": "gallery-dl 触发频率限制——Instagram 暂时拦截。\n请等待 5–10 分钟后重试。",
    "err.gdl_not_installed_short": "尚未安装 gallery-dl。\n请运行：pip install gallery-dl",
    "err.gdl_private": "该内容为私密——\n需要有权查看该内容的账号 Cookie。",
    "err.gdl_unknown": "gallery-dl 失败，原因未知。",
    "err.gdl_not_installed": "尚未安装 gallery-dl。\n请运行：pip install gallery-dl\n然后重启 OmniDL。",
    "err.gdl_no_content": "gallery-dl 在该 URL 未找到任何内容。\n请检查 URL，或刷新 Cookie。",
    "err.fb_post_advert_only": (
        "该 Facebook 帖子只包含图片。\n"
        "yt-dlp 找到的唯一视频是 Facebook 注入页面的广告，并非帖子本身的内容。\n"
        "请在设置 → 网络中刷新 Facebook Cookie 后重试。"
    ),
    "err.gdl_timeout": "gallery-dl 读取 URL 信息时超时。",
    "err.gdl_missing_binary": "找不到 gallery-dl。\n请安装：pip install gallery-dl",
    "gdl.photo_count": "{count} 张图片",
    "progress.gdl_preparing": "⬇ 正在准备下载图片…",
    "progress.gdl_video_audio": "⬇ 正在下载带音频的视频…",
    "progress.gdl_downloaded": "⬇ 已下载 {count} 个文件",
    "err.cdn_file_too_small": "下载的文件过小——CDN 链接可能已过期。",
    "err.cdn_link_expired": "CDN 链接已过期（oe= 签名失效）。请从浏览器复制新的链接。",
    "err.cdn_forbidden": "CDN 链接访问被拒绝（403）。",
    "err.ks_strategy_e_platform": (
        "Kuaishou：最后的兜底方案（Strategy E）仅支持 Windows 和 macOS。\n"
        "\n"
        "可在 设置 → 网络 中配置 Kuaishou Cookie，以启用其他提取方式。"
    ),
    "err.ks_all_strategies_failed": (
        "Kuaishou：所有提取方式均失败。\n"
        "\n"
        "可能原因：\n"
        "• 视频已删除或为私密\n"
        "• Kuaishou 屏蔽了当前 IP 的请求\n"
        "• Kuaishou 页面结构已变更\n"
        "\n"
        "可在 设置 → 网络 中配置 Kuaishou Cookie，以启用更多提取方式。"
    ),
    "err.ks_no_video_url": "Kuaishou：页面数据中未找到视频 URL。\n视频可能有地区限制，或 API 已变更。",
    "err.ks_bad_file": "下载的文件无效（不是 MP4 或过小）。CDN 链接可能已过期——请重试。",
    "err.ks_no_photo_id": "无法从该 URL 解析 photo_id：{url}\n请检查 Kuaishou 链接。",
    "err.ks_cancelled": "Kuaishou：已取消。",
    "err.ks_no_browser": (
        "Kuaishou：本机未找到 Brave 或 Chrome。\n"
        "\n"
        "最后的兜底方案（Strategy E）需要其中之一来打开 Kuaishou 页面并拦截真实 CDN 链接。\n"
        "\n"
        "请安装 Brave（https://brave.com）或 Google Chrome 后重试。\n"
        "安装后无需额外配置——OmniDL 会自动查找。"
    ),
    "err.ks_cdn_http": "Kuaishou CDN 返回 HTTP {code}。CDN 链接可能已过期——请重试。",
    "err.ks_cdn_http_after_reextract": (
        "重新提取后 Kuaishou CDN 仍返回 HTTP {code}。CDN 链接可能已过期——请重试。"
    ),
    "err.ks_cdn_html_after_reextract": "重新提取后 Kuaishou CDN 仍返回 HTML——IP 被封锁，或视频已不可用。",
    "err.ks_cdn_html_no_page_url": (
        "Kuaishou CDN 返回了 HTML，但无法确定用于重新提取的页面 URL。\n"
        "Remote API 需要传入 Kuaishou 页面 URL，而不是 CDN URL。"
    ),
    "err.ks_no_page_url": (
        "Kuaishou：无法确定用于重新提取的页面 URL。\n"
        "task.url 看起来像 CDN URL（video_id={video_id}）——Remote API 需要传入页面 URL，而不是 CDN URL。"
    ),
    "err.waaw_cdn_expired": "CDN 链接已过期或被 IP 锁定 - 请重新打开 waaw.ac/f/... 页面以获取新链接",
    "err.waaw_platform": "waaw.ac 引擎需要 Windows 或 macOS。",
    "err.waaw_platform_linux": "waaw.ac 引擎需要 Windows 或 macOS。\n暂不支持 Linux。",
    "err.waaw_file_too_small": "下载的文件过小（< 10 KB）——CDN 可能已拦截。",
    "err.waaw_not_mp4": "下载的文件不是 MP4——CDN 链接可能已过期。",
    "err.waaw_hls_no_fallback": "无法下载 HLS 流，且没有备用的 MP4 链接。",
    "err.waaw_hls_failed": "无法下载 HLS 流（ffmpeg：{tail}；MP4 备用：{err}）",
    "err.waaw_captcha_timeout": (
        "未在规定时间内完成验证码。\n"
        "请重试，并在弹出的浏览器窗口中完成验证码，或粘贴从其他渠道获取的新 CDN 链接（例如 cf*cdn.com 的 "
        ".m3u8）。"
    ),
    "err.waaw_no_cdn_url": "{seconds} 秒后仍未找到 CDN 链接。\nwaaw.ac 的防护机制可能已变更。",
    "err.generic_error_word": "错误",
    "err.cancelled_by_user": "用户已取消。",
    "err.playwright_missing": "缺少 Playwright 库。\n请运行：pip install playwright",
    "err.cdp_connect_failed": "无法通过 CDP 连接。\n请完全关闭浏览器后重试。\n（详情：{err}）",
    "err.cdp_connect_failed_hard": (
        "无法通过 CDP 连接。\n"
        "\n"
        "请完全关闭浏览器（包括系统托盘）后重试。\n"
        "（详情：{err}）"
    ),
    "err.browser_not_found_cdp": (
        "找不到 {browser}。请先安装该浏览器。\n"
        "注意：App Store 版本不支持 CDP——请使用官网版本。"
    ),
    "err.fb_not_story_url": "这不是 Facebook Story 链接。\n请粘贴形如 facebook.com/stories/... 的链接。",
    "err.no_download_dir": "尚未设置下载目录",
    "err.fb_story_platform": "Facebook Story 仅支持 Windows 和 macOS。\n暂不支持 Linux。",
    "err.fb_story_browser_running": (
        "{browser} 正在运行。请完全关闭 {browser} 后再次点击下载。\n"
        "原因：每个配置文件只能运行一个浏览器实例，{browser} 打开时 OmniDL 无法开启调试端口。"
    ),
    "err.fb_story_no_video_url": (
        "未能捕获 Story 的视频链接。\n"
        "\n"
        "可能原因：\n"
        "• Story 已过期（Stories 仅保留 24 小时）\n"
        "• 你未在 Brave/Chrome 中登录 Facebook\n"
        "• 该 Story 只有图片（没有视频）\n"
        "\n"
        "请先在浏览器中打开 Story 确认。"
    ),
    "err.fb_story_incomplete_download": (
        "已捕获视频链接，但文件未能完整下载。\n"
        "\n"
        "常见原因：\n"
        "• CDN 链接已过期（加载时间过长）\n"
        "• 网络连接不稳定\n"
        "\n"
        "请在浏览器中打开 Story 后立即重试。"
    ),
    "progress.browser_start": "正在启动浏览器…",
    "progress.browser_start_named": "正在启动 {browser}…",
    "progress.cdp_connect": "正在通过 CDP 连接…",
    "progress.waaw_open_page": "正在打开 waaw.ac 页面…",
    "progress.waaw_wait_cdn": "正在等待 CDN 链接…",
    "progress.waaw_captcha": "页面要求验证码——请在刚打开的浏览器窗口中完成验证…",
    "progress.ffmpeg_hls": "ffmpeg 正在下载 HLS 流…",
    "progress.ffmpeg_dash": "ffmpeg 正在处理 DASH 流…",
    "progress.ffmpeg_dash_fetch": "FFmpeg 正在从 DASH 获取视频+音频…",
    "progress.ffmpeg_merge": "FFmpeg 正在合并视频 + 音频…",
    "progress.ks_browser_start": "Kuaishou：正在启动浏览器…",
    "progress.ks_cdp_connect": "Kuaishou：正在通过 CDP 连接…",
    "progress.ks_open_page": "Kuaishou：正在打开视频页面…",
    "progress.fb_open_story": "正在浏览器中打开 Story…",
    "progress.wait_video": "正在等待视频加载…",
    "progress.url_captured": "已捕获链接——正在下载…",
    "progress.downloading_video": "正在下载视频…",
    "progress.downloading_kb": "正在下载… {kb} KB",
    "progress.done_named": "✅ 已完成！{name}",
    "nav.docs": "文档",
    "docs.title": "文档转换",
    "docs.subtitle": "Markdown ↔ PDF · HTML ↔ PDF · Office ↔ PDF",
    "docs.add_file": "添加文档",
    "docs.remove_selected": "移除所选",
    "docs.clear": "全部清除",
    "docs.source_label": "源文档：",
    "docs.target_label": "目标格式：",
    "docs.save_dir_label": "保存到：",
    "docs.choose": "选择…",
    "docs.convert": "转换",
    "docs.cancel": "取消",
    "docs.page_n": "第 {n} 页",
    "docs.file_filter": "文档 ({exts});;所有文件 (*)",
    "docs.no_file_title": "未选择文档",
    "docs.no_file_msg": "请至少添加一个待转换的文档。",
    "docs.no_target_title": "没有共同的目标格式",
    "docs.no_target_msg": "所选文档没有共同的目标格式，请分批转换。",
    "docs.status.converting": "正在转换 {name}…（{i}/{total}）",
    "docs.status.done": "完成：{ok}/{total} 个文档 → {dir}",
    "docs.status.failed": "转换失败",
    "docs.status.cancelled": "已取消转换",
    "docs.done_title": "已完成",
    "docs.done_msg": "已转换 {ok}/{total} 个文档。\n是否打开输出文件夹？",
    "docs.error_title": "转换错误",
    "docs.caps_title": "本机可用功能",
    "docs.cap.markdown": "Markdown → HTML（markdown 包）",
    "docs.cap.weasyprint": "HTML → PDF（WeasyPrint）",
    "docs.cap.pypdf": "PDF → 文本（pypdf）",
    "docs.cap.libreoffice": "Office ↔ PDF（LibreOffice）",
    "docs.cap_ok": "可用",
    "docs.cap_missing": "未安装",
    "docs.libreoffice_hint": "安装 LibreOffice 以启用 Office ↔ PDF 转换（.docx、.xlsx、.pptx、.odt…）。",
    "docs.recheck": "重新检测",
    "docs.weasyprint_hint": (
        "未找到 PDF 渲染库（Pango/GTK）。在 Windows 上请安装 "
        "GTK for Windows Runtime 以启用 Markdown/HTML → PDF。"
    ),
}

CATALOG: dict[str, dict[str, str]] = {"en": EN, "vi": VI, "zh": ZH}
