# Chuyển đổi tài liệu

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
