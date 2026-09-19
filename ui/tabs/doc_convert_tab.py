"""Documents tab — convert Markdown / HTML / Office documents to and from PDF."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.doc_convert_service import (
    SOURCE_EXTS,
    TARGET_FORMATS,
    DocConvertCancelled,
    DocConvertService,
    capabilities,
    targets_for,
)
from ui.components.progress_bar import OmniProgressBar
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.helpers import open_folder
from utils.i18n import t
from utils.soffice_locator import reset_soffice_cache

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_FMT_LABELS: dict[str, str] = {
    "pdf": "PDF (.pdf)",
    "html": "HTML (.html)",
    "md": "Markdown (.md)",
    "docx": "Word (.docx)",
}


class DocConvertTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._service = DocConvertService()
        self._sources: list[Path] = []
        self._busy = False
        self._cancel_event: Optional[threading.Event] = None
        self._caps_probed = False
        self._build()
        self._refresh_targets()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Probing the back-ends imports WeasyPrint, which costs seconds. Every
        # tab is constructed at startup, so doing it in __init__ would add that
        # to launch time for users who never open this tab.
        if not self._caps_probed:
            self._caps_probed = True
            self._refresh_capabilities()
        self._fade_anim.stop()
        self._fade_anim.start()

    # ── Layout ───────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QVBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 10)
        hdr_layout.setSpacing(4)

        self._title_lbl = QLabel(t("docs.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(t("docs.subtitle"))
        self._subtitle_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        hdr_layout.addWidget(self._subtitle_lbl)
        layout.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(28, 0, 28, 20)
        c_layout.setSpacing(14)

        c_layout.addWidget(self._build_convert_panel())
        c_layout.addWidget(self._build_caps_panel())

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        self._status_lbl.setWordWrap(True)
        c_layout.addWidget(self._status_lbl)

        progress_row = QHBoxLayout()
        self._progress = OmniProgressBar()
        self._progress.hide()
        progress_row.addWidget(self._progress, 1)
        self._cancel_btn = QPushButton(t("docs.cancel"))
        self._cancel_btn.setObjectName("danger")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self._cancel_current_operation)
        progress_row.addWidget(self._cancel_btn)
        c_layout.addLayout(progress_row)

        c_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _build_convert_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        self._src_lbl = QLabel(t("docs.source_label"))
        v.addWidget(self._src_lbl)

        src_row = QHBoxLayout()
        self._add_file_btn = QPushButton(t("docs.add_file"))
        self._add_file_btn.clicked.connect(self._browse_files)
        self._remove_btn = QPushButton(t("docs.remove_selected"))
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn = QPushButton(t("docs.clear"))
        self._clear_btn.clicked.connect(self._clear_sources)
        for b in (self._add_file_btn, self._remove_btn, self._clear_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            src_row.addWidget(b)
        src_row.addStretch()
        v.addLayout(src_row)

        self._sources_list = QListWidget()
        self._sources_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._sources_list.setFixedHeight(140)
        v.addWidget(self._sources_list)

        fmt_row = QHBoxLayout()
        self._target_lbl = QLabel(t("docs.target_label"))
        fmt_row.addWidget(self._target_lbl)
        self._target_combo = QComboBox()
        fmt_row.addWidget(self._target_combo)
        fmt_row.addStretch()
        v.addLayout(fmt_row)

        out_row = QHBoxLayout()
        self._out_dir_lbl = QLabel(t("docs.save_dir_label"))
        out_row.addWidget(self._out_dir_lbl)
        self._output_dir_entry = QLineEdit(str(self._app.config.download_dir))
        out_row.addWidget(self._output_dir_entry)
        self._out_browse_btn = QPushButton(t("docs.choose"))
        self._out_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._out_browse_btn.clicked.connect(self._browse_output_dir)
        out_row.addWidget(self._out_browse_btn)
        v.addLayout(out_row)

        self._convert_btn = QPushButton(t("docs.convert"))
        self._convert_btn.setObjectName("primary")
        self._convert_btn.setFixedHeight(34)
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.clicked.connect(self._start_convert)
        v.addWidget(self._convert_btn)

        return panel

    def _build_caps_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(6)

        head_row = QHBoxLayout()
        self._caps_title_lbl = QLabel(t("docs.caps_title"))
        self._caps_title_lbl.setStyleSheet("font-weight: 600;")
        head_row.addWidget(self._caps_title_lbl)
        head_row.addStretch()
        self._recheck_btn = QPushButton(t("docs.recheck"))
        self._recheck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recheck_btn.clicked.connect(self._on_recheck)
        head_row.addWidget(self._recheck_btn)
        v.addLayout(head_row)

        self._cap_labels: dict[str, QLabel] = {}
        for key in ("markdown", "weasyprint", "pypdf", "libreoffice"):
            lbl = QLabel("")
            lbl.setStyleSheet("font-size: 12px;")
            self._cap_labels[key] = lbl
            v.addWidget(lbl)

        self._wp_hint_lbl = QLabel(t("docs.weasyprint_hint"))
        self._lo_hint_lbl = QLabel(t("docs.libreoffice_hint"))
        for lbl in (self._wp_hint_lbl, self._lo_hint_lbl):
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
            v.addWidget(lbl)

        return panel

    # ── State helpers ────────────────────────────────────────────────────

    def _refresh_capabilities(self) -> None:
        caps = capabilities()
        flags = {
            "markdown": caps.markdown,
            "weasyprint": caps.weasyprint,
            "pypdf": caps.pypdf,
            "libreoffice": caps.libreoffice,
        }
        for key, lbl in self._cap_labels.items():
            ok = flags[key]
            mark = "✔" if ok else "✖"
            state = t("docs.cap_ok") if ok else t("docs.cap_missing")
            colour = T.success if ok else T.text3
            lbl.setText(f"{mark}  {t('docs.cap.' + key)} — {state}")
            lbl.setStyleSheet(f"color: {colour}; font-size: 12px;")
        self._lo_hint_lbl.setVisible(not caps.libreoffice)
        self._wp_hint_lbl.setVisible(not caps.weasyprint)

    def _refresh_targets(self) -> None:
        """Show only the formats every queued document can actually reach."""
        allowed: Optional[set[str]] = None
        for src in self._sources:
            reachable = set(targets_for(src.suffix))
            allowed = reachable if allowed is None else (allowed & reachable)
        options = [f for f in TARGET_FORMATS if allowed is None or f in allowed]

        previous = self._target_combo.currentData()
        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        for fmt in options:
            self._target_combo.addItem(_FMT_LABELS.get(fmt, fmt.upper()), fmt)
        if previous in options:
            self._target_combo.setCurrentIndex(options.index(previous))
        self._target_combo.blockSignals(False)
        self._target_combo.setEnabled(bool(options))

    def _set_busy(self, busy: bool, cancellable: bool = False) -> None:
        self._busy = busy
        for w in (
            self._convert_btn,
            self._add_file_btn,
            self._remove_btn,
            self._clear_btn,
            self._target_combo,
            self._out_browse_btn,
            self._output_dir_entry,
            self._recheck_btn,
        ):
            w.setEnabled(not busy)
        self._progress.setVisible(busy)
        self._cancel_btn.setVisible(busy and cancellable)
        if busy:
            self._progress.set_progress(0)
            self._progress.set_state("active")
        else:
            self._cancel_event = None
            self._refresh_targets()

    def _cancel_current_operation(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _on_recheck(self) -> None:
        self._caps_probed = True
        reset_soffice_cache()
        self._refresh_capabilities()
        self._refresh_targets()

    # ── File pickers ─────────────────────────────────────────────────────

    def _browse_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SOURCE_EXTS))
        files, _ = QFileDialog.getOpenFileNames(
            self, t("docs.add_file"), filter=t("docs.file_filter", exts=exts)
        )
        for f in files:
            p = Path(f)
            if p not in self._sources:
                self._sources.append(p)
                self._sources_list.addItem(str(p))
        self._refresh_targets()

    def _remove_selected(self) -> None:
        rows = sorted(
            (self._sources_list.row(item) for item in self._sources_list.selectedItems()), reverse=True
        )
        for idx in rows:
            self._sources_list.takeItem(idx)
            del self._sources[idx]
        self._refresh_targets()

    def _clear_sources(self) -> None:
        self._sources.clear()
        self._sources_list.clear()
        self._refresh_targets()

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("docs.save_dir_label"))
        if folder:
            self._output_dir_entry.setText(folder)

    # ── Convert ──────────────────────────────────────────────────────────

    def _start_convert(self) -> None:
        if self._busy:
            return
        if not self._sources:
            QMessageBox.warning(self, t("docs.no_file_title"), t("docs.no_file_msg"))
            return
        target = self._target_combo.currentData()
        if not target:
            QMessageBox.warning(self, t("docs.no_target_title"), t("docs.no_target_msg"))
            return

        out_dir = Path(self._output_dir_entry.text().strip() or str(self._app.config.download_dir))
        sources = list(self._sources)
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True, cancellable=True)

        def _worker() -> None:
            done: list[Path] = []
            errors: list[str] = []
            total = len(sources)
            for i, src in enumerate(sources, start=1):
                if cancel_event.is_set():
                    break
                ui_bridge.post(
                    lambda n=src.name, idx=i: self._status_lbl.setText(
                        t("docs.status.converting", name=n, i=idx, total=total)
                    )
                )

                def _on_progress(pct: float, idx: int = i) -> None:
                    overall = ((idx - 1) + pct / 100.0) / total * 100.0
                    ui_bridge.post(lambda p=overall: self._progress.set_progress(p))

                try:
                    done.append(
                        self._service.convert(
                            src,
                            target,
                            out_dir,
                            cancel_event=cancel_event,
                            on_progress=_on_progress,
                        )
                    )
                except DocConvertCancelled:
                    break
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                    logger.warning("Document conversion failed for %s: %s", src, exc)
                    errors.append(f"{src.name}: {exc}")

            ui_bridge.post(
                lambda d=done, e=errors: self._on_done(d, e, out_dir, total, cancel_event.is_set())
            )

        threading.Thread(target=_worker, daemon=True, name="omnidl-doc-convert").start()

    def _on_done(
        self,
        done: list[Path],
        errors: list[str],
        out_dir: Path,
        total: int,
        cancelled: bool,
    ) -> None:
        self._set_busy(False)

        if cancelled:
            self._progress.set_state("paused")
            self._status_lbl.setText(t("docs.status.cancelled"))
            return

        if not done:
            self._progress.set_state("failed")
            self._status_lbl.setText(t("docs.status.failed"))
            QMessageBox.critical(self, t("docs.error_title"), "\n".join(errors) or t("docs.status.failed"))
            return

        self._progress.set_progress(100)
        self._progress.set_state("complete" if not errors else "failed")
        self._status_lbl.setText(t("docs.status.done", ok=len(done), total=total, dir=out_dir))
        if errors:
            QMessageBox.warning(self, t("docs.error_title"), "\n".join(errors))
        reply = QMessageBox.question(
            self, t("docs.done_title"), t("docs.done_msg", ok=len(done), total=total)
        )
        if reply == QMessageBox.StandardButton.Yes:
            open_folder(out_dir)

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._title_lbl.setText(t("docs.title"))
        self._subtitle_lbl.setText(t("docs.subtitle"))
        self._src_lbl.setText(t("docs.source_label"))
        self._add_file_btn.setText(t("docs.add_file"))
        self._remove_btn.setText(t("docs.remove_selected"))
        self._clear_btn.setText(t("docs.clear"))
        self._target_lbl.setText(t("docs.target_label"))
        self._out_dir_lbl.setText(t("docs.save_dir_label"))
        self._out_browse_btn.setText(t("docs.choose"))
        self._convert_btn.setText(t("docs.convert"))
        self._cancel_btn.setText(t("docs.cancel"))
        self._caps_title_lbl.setText(t("docs.caps_title"))
        self._recheck_btn.setText(t("docs.recheck"))
        self._lo_hint_lbl.setText(t("docs.libreoffice_hint"))
        self._wp_hint_lbl.setText(t("docs.weasyprint_hint"))
        if self._caps_probed:
            self._refresh_capabilities()
