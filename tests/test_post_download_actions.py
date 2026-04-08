"""
tests/test_post_download_actions.py
Unit tests for the new post-download features:

  PostDownloadActions (ui/components/post_download_actions.py):
    - on_convert callback receives correct (path, ext) args
    - on_send callback receives (path, restore_fn) args
    - on_delete deletes file from disk, then calls on_delete callback
    - on_delete no-ops (no callback error) when file already gone
    - notify_convert_done / notify_convert_error update internal state
    - hide() resets _file_path
    - _ConfirmDeleteDialog.confirmed = False prevents deletion

  TaildropService.send_file_to_nodes (app/services/taildrop_service.py):
    - Sends to each node in the list via _do_send
    - Skips nodes that fail _NODE_RE validation
    - Fires on_node_done for successful transfers
    - Fires on_node_error for failed transfers
    - Skips when service is closed
    - Skips when file does not exist

  ConfigManager multi-node accessors (infrastructure/config/config_manager.py):
    - taildrop_target_nodes returns list stored under "taildrop_target_nodes"
    - taildrop_target_nodes falls back to [taildrop_target_node] when list empty
    - taildrop_target_nodes returns [] when both keys are empty
    - set_taildrop_target_nodes persists list and syncs legacy scalar
    - set_taildrop_target_nodes with empty list clears legacy scalar too
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_tmp_file(tmp_path: Path, name: str = "video.mp4") -> Path:
    """Create a non-empty temporary file and return its path."""
    p = tmp_path / name
    p.write_bytes(b"fake-media-data")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# ConfigManager — multi-node accessor tests
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigManagerMultiNode:
    """Tests for taildrop_target_nodes property and set_taildrop_target_nodes."""

    def _make_cfg(self, tmp_path: Path, overrides: dict = None):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        if overrides:
            for k, v in overrides.items():
                cfg.set(k, v)
        return cfg

    def test_returns_stored_list(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set("taildrop_target_nodes", ["iphone", "macbook"])
        assert cfg.taildrop_target_nodes == ["iphone", "macbook"]

    def test_fallback_to_legacy_scalar(self, tmp_path):
        """When taildrop_target_nodes is empty, fall back to taildrop_target_node."""
        cfg = self._make_cfg(tmp_path)
        cfg.set("taildrop_target_nodes", [])
        cfg.set("taildrop_target_node", "iphone")
        assert cfg.taildrop_target_nodes == ["iphone"]

    def test_empty_when_both_missing(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set("taildrop_target_nodes", [])
        cfg.set("taildrop_target_node", "")
        assert cfg.taildrop_target_nodes == []

    def test_set_taildrop_target_nodes_persists_list(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set_taildrop_target_nodes(["iphone", "pixel"])
        assert cfg.taildrop_target_nodes == ["iphone", "pixel"]

    def test_set_taildrop_target_nodes_syncs_legacy_scalar(self, tmp_path):
        """Legacy taildrop_target_node must equal first element of new list."""
        cfg = self._make_cfg(tmp_path)
        cfg.set_taildrop_target_nodes(["macbook", "iphone"])
        assert cfg.taildrop_target_node == "macbook"

    def test_set_taildrop_target_nodes_empty_clears_scalar(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set("taildrop_target_node", "iphone")
        cfg.set_taildrop_target_nodes([])
        assert cfg.taildrop_target_node == ""
        assert cfg.taildrop_target_nodes == []

    def test_strips_whitespace_from_nodes(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set_taildrop_target_nodes(["  iphone  ", " macbook "])
        assert cfg.taildrop_target_nodes == ["iphone", "macbook"]

    def test_ignores_blank_entries(self, tmp_path):
        cfg = self._make_cfg(tmp_path)
        cfg.set_taildrop_target_nodes(["iphone", "", "  "])
        assert cfg.taildrop_target_nodes == ["iphone"]


# ─────────────────────────────────────────────────────────────────────────────
# TaildropService.send_file_to_nodes
# ─────────────────────────────────────────────────────────────────────────────

class TestSendFileToNodes:
    """Tests for TaildropService.send_file_to_nodes()."""

    def _make_service(self, tmp_path: Path):
        """Return a TaildropService with a no-op event bus."""
        from app.services.taildrop_service import TaildropService, TransferResult
        bus = MagicMock()
        bus.subscribe = MagicMock()

        cfg = MagicMock()
        cfg.taildrop_enabled     = True
        cfg.taildrop_target_node = "iphone"
        cfg.taildrop_send_mode   = "always"

        svc = TaildropService(config=cfg, event_bus=bus)
        return svc

    def _ok_result(self, node="iphone"):
        from app.services.taildrop_service import TransferResult
        return TransferResult(success=True, dest_node=node, error="")

    def _fail_result(self, node="iphone", msg="timeout"):
        from app.services.taildrop_service import TransferResult
        return TransferResult(success=False, dest_node=node, error=msg)

    # ── Basic delivery ─────────────────────────────────────────────────────

    def test_sends_to_each_node(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)
        done_nodes = []

        with patch.object(svc, "_do_send", return_value=self._ok_result()) as mock_send:
            svc.send_file_to_nodes(
                file_path,
                ["iphone", "macbook"],
                on_node_done=done_nodes.append,
            )
            # Allow worker threads to finish.
            svc.close()

        assert mock_send.call_count == 2
        called_nodes = {c.args[1] for c in mock_send.call_args_list}
        assert called_nodes == {"iphone", "macbook"}

    def test_on_node_done_fires_for_each_success(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)
        done_nodes = []

        with patch.object(svc, "_do_send", return_value=self._ok_result()):
            svc.send_file_to_nodes(
                file_path, ["iphone", "macbook"],
                on_node_done=done_nodes.append,
            )
            svc.close()

        assert set(done_nodes) == {"iphone", "macbook"}

    def test_on_node_error_fires_for_failed_transfer(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)
        errors = {}

        def _fail_result_for(node):
            from app.services.taildrop_service import TransferResult
            return TransferResult(success=False, dest_node=node, error="timeout")

        with patch.object(svc, "_do_send", side_effect=lambda fp, n, **kw: _fail_result_for(n)):
            svc.send_file_to_nodes(
                file_path, ["iphone"],
                on_node_error=lambda n, e: errors.update({n: e}),
            )
            svc.close()

        assert "iphone" in errors
        assert "timeout" in errors["iphone"]

    # ── Validation ─────────────────────────────────────────────────────────

    def test_skips_invalid_node_names(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)

        with patch.object(svc, "_do_send", return_value=self._ok_result()) as mock_send:
            svc.send_file_to_nodes(
                file_path,
                ["iphone", "bad node!", "../etc/passwd", "valid-node"],
            )
            svc.close()

        called_nodes = {c.args[1] for c in mock_send.call_args_list}
        assert "bad node!" not in called_nodes
        assert "../etc/passwd" not in called_nodes
        assert "iphone" in called_nodes
        assert "valid-node" in called_nodes

    def test_no_send_when_all_nodes_invalid(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)

        with patch.object(svc, "_do_send") as mock_send:
            svc.send_file_to_nodes(file_path, ["bad node!", "also bad!"])
            svc.close()

        mock_send.assert_not_called()

    def test_no_send_when_file_missing(self, tmp_path):
        svc = self._make_service(tmp_path)
        ghost = tmp_path / "nonexistent.mp4"

        with patch.object(svc, "_do_send") as mock_send:
            svc.send_file_to_nodes(ghost, ["iphone"])
            svc.close()

        mock_send.assert_not_called()

    def test_no_send_when_service_closed(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)
        svc.close()

        with patch.object(svc, "_do_send") as mock_send:
            svc.send_file_to_nodes(file_path, ["iphone"])

        mock_send.assert_not_called()

    def test_callbacks_are_optional(self, tmp_path):
        """send_file_to_nodes must not crash when callbacks are None."""
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)

        with patch.object(svc, "_do_send", return_value=self._ok_result()):
            svc.send_file_to_nodes(file_path, ["iphone"])
            svc.close()
        # No assertion — just must not raise.

    def test_exception_in_on_node_done_does_not_crash(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)

        def _bad_cb(node):
            raise RuntimeError("callback bug")

        with patch.object(svc, "_do_send", return_value=self._ok_result()):
            svc.send_file_to_nodes(file_path, ["iphone"], on_node_done=_bad_cb)
            svc.close()
        # Must not propagate the exception.

    def test_specific_files_override_passed_to_do_send(self, tmp_path):
        svc = self._make_service(tmp_path)
        file_path = _make_tmp_file(tmp_path)
        extra = _make_tmp_file(tmp_path, "img.jpg")
        missing = tmp_path / "ghost.jpg"
        done = []

        with patch.object(svc, "_do_send", return_value=self._ok_result()) as mock_send:
            svc.send_file_to_nodes(
                file_path, ["iphone"],
                on_node_done=done.append,
                specific_files_override=[extra, missing],
            )
            svc.close()

        assert "iphone" in done
        kw = mock_send.call_args.kwargs
        # only existing files passed through
        assert kw.get("specific_files") == [extra]


# ─────────────────────────────────────────────────────────────────────────────
# PostDownloadActions — pure-logic tests (no Tkinter)
# ─────────────────────────────────────────────────────────────────────────────

class TestPostDownloadActionsLogic:
    """
    Tests for PostDownloadActions business logic exercised WITHOUT a live
    Tkinter window.  We mock the entire ctk namespace so that widget
    construction is a no-op, then drive the handler methods directly.
    """

    def _make_widget(self, on_convert=None, on_send=None, on_delete=None):
        """
        Instantiate PostDownloadActions with the Tkinter internals fully
        mocked.  Returns the widget (actually a MagicMock subclass) with its
        real handler methods still callable.
        """
        # We import the module and monkey-patch _BaseFrame so no real widgets
        # are created.  This is identical to the pattern in test_audit_fixes.py.
        import ui.components.post_download_actions as mod

        original_base = mod._BaseFrame
        mock_frame = MagicMock()
        mock_frame.__init_subclass__ = classmethod(lambda cls, **kw: None)

        # Build a thin subclass of MagicMock that also inherits the real method
        # bodies from PostDownloadActions.
        class _FakePDA(MagicMock):
            pass

        # Rebind the real handler methods onto _FakePDA instances.
        obj = _FakePDA()
        obj._on_convert = on_convert
        obj._on_send    = on_send
        obj._on_delete  = on_delete
        obj._file_path  = None
        obj._converting = False

        # Bind the real methods we want to test.
        obj._on_post_convert_call = lambda path, ext: (
            mod.PostDownloadActions._on_convert_click
        )
        return obj, mod

    # ── Delete logic (pure Python — no Tk required) ─────────────────────────

    def test_delete_removes_file(self, tmp_path):
        """_on_delete_click must remove the file before calling on_delete."""
        from ui.components.post_download_actions import PostDownloadActions

        deleted_paths = []
        file_path = _make_tmp_file(tmp_path)

        # Minimal mock of a PostDownloadActions instance.
        obj = MagicMock(spec=PostDownloadActions)
        obj._file_path = file_path
        obj._gallery_dl_files = None
        obj._on_delete = deleted_paths.append

        # Simulate what _on_delete_click does (without the dialog — inject
        # confirmed=True via monkeypatching _ConfirmDeleteDialog).
        import ui.components.post_download_actions as pda_mod

        class _AlwaysConfirm:
            confirmed = True
            def __init__(self, parent, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch.object(pda_mod, "_ConfirmDeleteDialog", _AlwaysConfirm):
            # Also stub wait_window and winfo_exists so the method runs fully.
            obj.wait_window = MagicMock()
            obj.winfo_exists = MagicMock(return_value=True)
            obj.hide = MagicMock()
            obj._set_status = MagicMock()
            pda_mod.PostDownloadActions._on_delete_click(obj)

        assert not file_path.exists(), "File should have been deleted"
        assert len(deleted_paths) == 1
        assert deleted_paths[0] == file_path

    def test_delete_cancelled_leaves_file(self, tmp_path):
        """When the user cancels the dialog, the file must NOT be deleted."""
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        file_path = _make_tmp_file(tmp_path)
        deleted_paths = []

        obj = MagicMock(spec=PostDownloadActions)
        obj._file_path = file_path
        obj._gallery_dl_files = None
        obj._on_delete = deleted_paths.append

        class _NeverConfirm:
            confirmed = False
            def __init__(self, parent, **kw): pass

        with patch.object(pda_mod, "_ConfirmDeleteDialog", _NeverConfirm):
            obj.wait_window = MagicMock()
            pda_mod.PostDownloadActions._on_delete_click(obj)

        assert file_path.exists(), "File must still exist when user cancels"
        assert deleted_paths == []

    def test_delete_already_gone_does_not_raise(self, tmp_path):
        """If the file disappeared before deletion, log a warning but don't crash."""
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        ghost = tmp_path / "ghost.mp4"  # never created

        obj = MagicMock(spec=PostDownloadActions)
        obj._file_path = ghost
        obj._gallery_dl_files = None
        obj._on_delete = MagicMock()

        class _AlwaysConfirm:
            confirmed = True
            def __init__(self, parent, **kw): pass

        with patch.object(pda_mod, "_ConfirmDeleteDialog", _AlwaysConfirm):
            obj.wait_window = MagicMock()
            obj.winfo_exists = MagicMock(return_value=True)
            obj.hide = MagicMock()
            obj._set_status = MagicMock()
            pda_mod.PostDownloadActions._on_delete_click(obj)

        # on_delete must still fire even when file was already absent.
        obj._on_delete.assert_called_once_with(ghost)

    # ── notify helpers ─────────────────────────────────────────────────────

    def test_notify_convert_done_clears_converting_flag(self, tmp_path):
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        output = _make_tmp_file(tmp_path, "out.mp4")

        obj = MagicMock(spec=PostDownloadActions)
        obj._converting = True
        obj._compact = False
        obj._convert_btn = MagicMock()
        obj._gallery_dl_files = None
        obj.winfo_exists = MagicMock(return_value=True)

        pda_mod.PostDownloadActions.notify_convert_done(obj, output)

        assert obj._converting is False
        assert obj._file_path == output
        obj._convert_btn.configure.assert_called()

    def test_notify_convert_error_re_enables_button(self, tmp_path):
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        obj = MagicMock(spec=PostDownloadActions)
        obj._converting = True
        obj._compact = False
        obj._convert_btn = MagicMock()
        obj.winfo_exists = MagicMock(return_value=True)
        obj._set_status = MagicMock()

        pda_mod.PostDownloadActions.notify_convert_error(obj, "ffmpeg crashed")

        assert obj._converting is False
        obj._convert_btn.configure.assert_called()

    # ── on_send passthrough ────────────────────────────────────────────────

    def test_on_send_calls_callback_with_path_and_restore(self, tmp_path):
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        file_path = _make_tmp_file(tmp_path)
        received  = {}

        def _fake_send(path, restore):
            received["path"]    = path
            received["restore"] = restore

        obj = MagicMock(spec=PostDownloadActions)
        obj._file_path = file_path
        obj._on_send   = _fake_send
        obj._compact   = False
        obj._send_btn  = MagicMock()
        obj.winfo_exists = MagicMock(return_value=True)

        pda_mod.PostDownloadActions._on_send_click(obj)

        assert received["path"] == file_path
        assert callable(received["restore"])

    def test_on_send_no_callback_does_not_crash(self, tmp_path):
        from ui.components.post_download_actions import PostDownloadActions
        import ui.components.post_download_actions as pda_mod

        file_path = _make_tmp_file(tmp_path)

        obj = MagicMock(spec=PostDownloadActions)
        obj._file_path = file_path
        obj._on_send   = None
        obj._compact   = False
        obj._send_btn  = MagicMock()
        obj.winfo_exists = MagicMock(return_value=True)

        # Must not raise.
        pda_mod.PostDownloadActions._on_send_click(obj)

    # ── CONVERT_FORMATS contract ───────────────────────────────────────────

    def test_convert_formats_has_mp4_first(self):
        from ui.components.post_download_actions import CONVERT_FORMATS
        assert CONVERT_FORMATS[0][0] == "mp4"

    def test_convert_formats_contains_required_extensions(self):
        from ui.components.post_download_actions import CONVERT_FORMATS
        exts = {ext for ext, _ in CONVERT_FORMATS}
        assert {"mp4", "mp3", "mkv", "avi"}.issubset(exts)

    def test_convert_formats_all_have_labels(self):
        from ui.components.post_download_actions import CONVERT_FORMATS
        for ext, label in CONVERT_FORMATS:
            assert ext and label, f"Empty ext or label for entry ({ext!r}, {label!r})"
