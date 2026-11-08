# Document conversion

The **Documents** tab (and `POST /api/docs/convert`) converts between document
formats. Seven routes are supported:

| From | To | Back-end |
|---|---|---|
| Markdown (`.md`, `.markdown`, …) | PDF | `markdown` + WeasyPrint |
| Markdown | HTML | `markdown` |
| HTML (`.html`, `.htm`, `.xhtml`) | PDF | WeasyPrint |
| PDF | Markdown | `pypdf` |
| PDF | HTML | `pypdf` |
| PDF | DOCX | LibreOffice |
| Office (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.rtf`, `.csv`, …) | PDF | LibreOffice |

Two back-ends are **optional**, and OmniDL degrades gracefully without them —
`GET /api/docs/capabilities` reports what the host can do, the desktop tab greys
out the rest, and the API answers `503` with a plain-language reason:

- **LibreOffice** — required for every Office route. Not bundled (it is a ~700 MB
  suite); install it from [libreoffice.org](https://www.libreoffice.org/) and the
  tab's **Re-check** button picks it up without restarting OmniDL.
- **Pango / GTK** — WeasyPrint loads Pango, HarfBuzz and fontconfig at import time.
  Linux and macOS package managers ship these; on **Windows** install the
  [GTK for Windows Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer)
  to enable Markdown/HTML → PDF.

**Scanned PDFs are not supported.** PDF → Markdown/HTML extracts the text layer;
a PDF with no selectable text fails with a clear message rather than producing an
empty file. There is no OCR.

**Security.** A document can reference remote or arbitrary local resources through
`<img>`, `<link>`, `@import` or a CSS `url()`. The renderer allows only `data:`
URIs and files inside the source document's own folder; anything else is refused,
logged, and skipped (the page still renders without it). Over the Remote API both
`source_path` and `out_dir` are confined to `download_dir`, sources over 200 MiB
are rejected, and concurrency is bounded to 2.
