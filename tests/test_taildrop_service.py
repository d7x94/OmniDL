"""
tests/test_taildrop_service.py
Unit tests for TaildropService.

Coverage:
• _NODE_RE security validation (allowlist)
• TransferResult dataclass
• send_file: missing tailscale CLI → graceful error
• send_file: tailscale exits non-zero → error captured
• send_file: success path
• send_file: file not found
• send_file: subprocess timeout
• list_nodes: CLI not available → empty list
• list_nodes: parses tailscale status JSON correctly
• on_download_completed: disabled → no subprocess call
• on_download_completed: enabled, valid file → executor submit called
• on_download_completed: missing output_path → skip silently
• close(): executor shuts down cleanly
• Config typed properties: taildrop_enabled / taildrop_target_node / taildrop_send_mode
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.taildrop_service import TaildropService, TransferResult, _NODE_RE, _sanitize_filename


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(enabled=False, node="iphone", mode="always"):
    cfg = MagicMock()
    type(cfg).taildrop_enabled = property(lambda self: enabled)
    type(cfg).taildrop_target_node = property(lambda self: node)
    type(cfg).taildrop_send_mode = property(lambda self: mode)
    return cfg


def _make_bus():
    bus = MagicMock()
    return bus


def _make_task(output_path: str | None = None, filename: str | None = None):
    """Build a minimal mock DownloadTask for TaildropService tests.

    ``filename`` is the canonical DownloadTask field written by yt_dlp_engine's
    pp_hook.  ``output_path`` and ``file_path`` are legacy aliases kept for
    backward-compatibility tests; in production they are always None on a real
    DownloadTask (the field does not exist and getattr returns None).

    When both ``filename`` and ``output_path`` are supplied, ``filename`` wins
    because it is checked first in on_download_completed().
    """
    task = MagicMock()
    task.id = "task-001"
    # canonical field — matches DownloadTask.filename set by yt_dlp_engine
    task.filename = filename
    # legacy aliases — not present on real DownloadTask (getattr → None)
    task.output_path = output_path
    task.file_path = None
    return task


def _make_svc(enabled=False, node="iphone") -> TaildropService:
    return TaildropService(config=_make_config(enabled, node), event_bus=_make_bus())


# ─────────────────────────────────────────────────────────────────────────────
# _NODE_RE security tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeRegex:
    """Validate allowlist regex blocks injection, accepts valid names."""

    VALID = [
        "iphone",
        "my-iphone",
        "pixel-7",
        "100.64.0.5",
        "iphone.tail1abc2.ts.net",
        "AB",                      # two-char minimum
    ]
    INVALID = [
        "",                        # empty
        " iphone",                 # leading space
        "iphone ",                 # trailing space
        "iphone; rm -rf /",        # shell injection
        "iphone && malware",       # shell injection
        "../../etc/passwd",        # path traversal
        "iphone|cat /etc/shadow",  # pipe injection
        "a" * 300,                 # too long
        "-leading-dash",           # must start with alnum
    ]

    @pytest.mark.parametrize("name", VALID)
    def test_valid_node_names(self, name):
        assert _NODE_RE.match(name), f"Expected VALID: {name!r}"

    @pytest.mark.parametrize("name", INVALID)
    def test_invalid_node_names(self, name):
        assert not _NODE_RE.match(name), f"Expected INVALID: {name!r}"


# ─────────────────────────────────────────────────────────────────────────────
# _sanitize_filename
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeFilename:
    """
    Unit tests for the _sanitize_filename() helper.

    Every case maps a raw yt-dlp filename to the expected Tailscale-safe
    alias.  ASCII-clean names must be returned unchanged so that the
    common path never triggers the ``--name`` flag unnecessarily.
    """

    def test_ascii_clean_name_unchanged(self):
        """Pure ASCII filename → returned as-is (no --name overhead)."""
        assert _sanitize_filename("video.mp4") == "video.mp4"

    def test_ascii_with_hyphens_unchanged(self):
        assert _sanitize_filename("my-video_2024.mp4") == "my-video_2024.mp4"

    def test_emoji_stripped(self):
        """Emoji must be removed entirely (they have no ASCII counterpart)."""
        result = _sanitize_filename("clip ❤️‍🔥 fun.mp4")
        assert "❤" not in result
        assert result.endswith(".mp4")

    def test_vietnamese_diacritics_transliterated(self):
        """Diacritics decompose to base ASCII letter via NFKD."""
        result = _sanitize_filename("Ba dím.mp4")
        assert "í" not in result
        assert "dim" in result or "d" in result  # "í" → "i" via NFKD
        assert result.endswith(".mp4")

    def test_hashtags_replaced(self):
        """# chars must not appear in the sanitized name."""
        result = _sanitize_filename("#dodonhatminh #vinschool.mp4")
        assert "#" not in result
        assert result.endswith(".mp4")

    def test_at_symbol_replaced(self):
        assert "@" not in _sanitize_filename("@username clip.mp4")

    def test_extension_preserved_exactly(self):
        """The file extension (.mp4, .mov, …) must survive sanitization."""
        assert _sanitize_filename("❤️video.mp4").endswith(".mp4")
        assert _sanitize_filename("❤️video.mov").endswith(".mov")

    def test_no_leading_or_trailing_underscores_in_stem(self):
        """Outer underscores from collapsed special chars should be stripped."""
        result = _sanitize_filename("###video###.mp4")
        stem = result[: result.rfind(".")]
        assert not stem.startswith("_")
        assert not stem.endswith("_")

    def test_multiple_spaces_collapsed(self):
        """Runs of spaces/underscores → single underscore."""
        result = _sanitize_filename("a   b    c.mp4")
        assert "  " not in result
        assert "__" not in result

    def test_empty_stem_fallback(self):
        """Filename composed entirely of emoji → stem becomes 'file'."""
        result = _sanitize_filename("❤️🔥.mp4")
        assert result == "file.mp4"

    def test_no_extension(self):
        """Filename with no dot is handled without IndexError."""
        result = _sanitize_filename("❤️video")
        assert "❤" not in result
        assert "." not in result

    def test_real_failing_filename_from_log(self):
        """
        Regression: the exact filename that caused 'invalid filename' in
        production (task 5b467261 / 66c3fd2f from omnidl_run.log).

        dodonhatminh109 - 2026-03-28 - Top 15 edurun 2026 ❤️‍🔥@Ba dím
          #dodonhatminh  #vinschool  #edurun  #... [762213827654].mp4
        """
        raw = (
            "dodonhatminh109 - 2026-03-28 - Top 15 edurun 2026 "
            "\u2764\ufe0f\u200d\U0001f525"   # ❤️‍🔥
            "@Ba d\xedm  "                    # @Ba dím
            "#dodonhatminh  #vinschool  #edurun  #... "
            "[762213827654].mp4"
        )
        result = _sanitize_filename(raw)

        # Must be pure ASCII.
        result.encode("ascii")  # raises UnicodeEncodeError if not

        # Must keep the extension.
        assert result.endswith(".mp4")

        # Must contain key readable parts from the original.
        assert "dodonhatminh109" in result
        assert "2026-03-28" in result
        assert "edurun" in result

        # Must not contain any of the problematic characters.
        for bad in ("#", "@", "❤", "🔥", "í"):
            assert bad not in result, f"Bad char {bad!r} still present in {result!r}"


# ─────────────────────────────────────────────────────────────────────────────
# send_file — --name flag injection
# ─────────────────────────────────────────────────────────────────────────────

class TestSendFileNameFlag:
    """
    Verify that _do_send() passes ``--name <safe>`` when the filename
    contains characters that Tailscale would reject, and omits ``--name``
    for clean ASCII filenames (to avoid unnecessary CLI flag noise).
    """

    def test_ascii_filename_no_name_flag(self, tmp_path):
        """Clean ASCII filename → tailscale call has NO --name flag."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone")

        ok = MagicMock()
        ok.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            r = svc.send_file(f, "iphone")

        assert r.success
        cmd = mock_run.call_args[0][0]
        assert "--name" not in cmd
        assert cmd == ["/usr/bin/tailscale", "file", "cp", str(f), "iphone:"]
        svc.close()

    def test_emoji_filename_uses_name_flag(self, tmp_path):
        """Filename with emoji → --name <safe_name> injected before the path."""
        raw_name = "clip ❤️🔥 fun.mp4"
        f = tmp_path / raw_name
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone")

        ok = MagicMock()
        ok.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            r = svc.send_file(f, "iphone")

        assert r.success
        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        safe_name = cmd[name_idx + 1]
        # Safe name must be pure ASCII and keep the extension.
        safe_name.encode("ascii")
        assert safe_name.endswith(".mp4")
        assert "❤" not in safe_name
        svc.close()

    def test_real_tiktok_filename_uses_name_flag(self, tmp_path):
        """
        Regression: the exact filename from omnidl_run.log triggers --name.
        """
        raw_name = (
            "dodonhatminh109 - 2026-03-28 - Top 15 edurun 2026 "
            "\u2764\ufe0f\u200d\U0001f525@Ba d\xedm  "
            "#dodonhatminh  #vinschool  #edurun  #... [762213827654].mp4"
        )
        f = tmp_path / raw_name
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone-12-pro-max")

        ok = MagicMock()
        ok.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            r = svc.send_file(f, "iphone-12-pro-max")

        assert r.success, f"Expected success; got error: {r.error}"
        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd, "Expected --name flag for non-ASCII filename"
        name_idx = cmd.index("--name")
        safe_name = cmd[name_idx + 1]
        safe_name.encode("ascii")   # must be pure ASCII — no UnicodeEncodeError
        assert safe_name.endswith(".mp4")
        # Original (unsafe) file path is still passed as the actual source
        assert str(f) in cmd
        svc.close()


# ─────────────────────────────────────────────────────────────────────────────
# TransferResult
# ─────────────────────────────────────────────────────────────────────────────


# -----------------------------------------------------------------------------
# Directory -> zip (BUG-BV)
# -----------------------------------------------------------------------------

class TestDirectoryZip:
    """
    _do_send() must zip a directory to a temp file, send the zip under
    --name <folder>.zip, and delete the temp file after the call.
    """

    def test_directory_is_zipped_and_sent(self, tmp_path):
        """Happy path: directory zipped, sent as <folder>.zip, temp cleaned up."""
        folder = tmp_path / "jossias_py"
        folder.mkdir()
        (folder / "img1.jpg").write_bytes(b"jpeg1")
        (folder / "img2.jpg").write_bytes(b"jpeg2")

        svc = _make_svc(enabled=True, node="iphone")
        ok = MagicMock()
        ok.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            r = svc.send_file(folder, "iphone")

        assert r.success
        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1].endswith(".zip")
        # Actual src passed to tailscale must be the temp zip, not the folder.
        actual_src = cmd[-2]
        assert actual_src != str(folder)
        # Temp file must be deleted after send.
        from pathlib import Path
        assert not Path(actual_src).exists()
        svc.close()

    def test_directory_zip_contains_files(self, tmp_path):
        """Zip created from directory must contain expected members."""
        import zipfile as _zf
        import shutil as _sh

        folder = tmp_path / "gallery"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"A")
        sub = folder / "sub"
        sub.mkdir()
        (sub / "b.png").write_bytes(b"B")

        captured_zip: list = []
        svc = _make_svc(enabled=True, node="iphone")
        ok = MagicMock()
        ok.returncode = 0

        def capturing_run(cmd, **kwargs):
            from pathlib import Path
            captured_zip.append(Path(cmd[-2]))
            _sh.copy2(cmd[-2], str(cmd[-2]) + ".bak")
            return ok

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", side_effect=capturing_run):
            svc.send_file(folder, "iphone")

        from pathlib import Path
        bak = Path(str(captured_zip[0]) + ".bak")
        assert bak.exists()
        with _zf.ZipFile(bak) as zf:
            names = zf.namelist()
        bak.unlink()

        assert any("a.jpg" in n for n in names)
        assert any("b.png" in n for n in names)
        svc.close()

    def test_directory_temp_deleted_on_failure(self, tmp_path):
        """Temp zip must be deleted even when tailscale returns non-zero."""
        folder = tmp_path / "myfolder"
        folder.mkdir()
        (folder / "x.jpg").write_bytes(b"x")

        svc = _make_svc(enabled=True, node="iphone")
        fail = MagicMock()
        fail.returncode = 1
        fail.stderr = "some error"
        fail.stdout = ""

        captured: list = []

        def capturing_run(cmd, **kwargs):
            from pathlib import Path
            captured.append(Path(cmd[-2]))
            return fail

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", side_effect=capturing_run):
            r = svc.send_file(folder, "iphone")

        assert not r.success
        assert captured
        assert not captured[0].exists(), "Temp zip must be deleted after failure"
        svc.close()

    def test_ascii_directory_name_uses_name_flag(self, tmp_path):
        """Even an ASCII-named directory must pass --name <folder>.zip."""
        folder = tmp_path / "photos"
        folder.mkdir()
        (folder / "img.jpg").write_bytes(b"data")

        svc = _make_svc(enabled=True, node="iphone")
        ok = MagicMock()
        ok.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            svc.send_file(folder, "iphone")

        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "photos.zip"
        svc.close()


class TestTransferResult:
    def test_success_result(self):
        r = TransferResult(success=True, dest_node="iphone")
        assert r.success is True
        assert r.error == ""

    def test_failure_result(self):
        r = TransferResult(success=False, dest_node="iphone", error="oops")
        assert r.success is False
        assert r.error == "oops"

    def test_frozen(self):
        r = TransferResult(success=True, dest_node="iphone")
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# send_file
# ─────────────────────────────────────────────────────────────────────────────

class TestSendFile:
    def test_invalid_node_rejected(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        r = svc.send_file(f, "bad node!")
        assert not r.success
        assert "security policy" in r.error
        svc.close()

    def test_file_not_found(self, tmp_path):
        svc = _make_svc()
        r = svc.send_file(tmp_path / "ghost.mp4", "iphone")
        assert not r.success
        assert "not found" in r.error
        svc.close()

    def test_tailscale_cli_missing(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        with patch("shutil.which", return_value=None):
            r = svc.send_file(f, "iphone")
        assert not r.success
        assert "not found on PATH" in r.error
        svc.close()

    def test_tailscale_nonzero_exit(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "node not found"
        mock_result.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=mock_result):
            r = svc.send_file(f, "iphone")
        assert not r.success
        assert "exit 1" in r.error
        svc.close()

    def test_tailscale_success(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            r = svc.send_file(f, "iphone")
        assert r.success
        assert r.dest_node == "iphone"
        # Verify CLI args: list form, no shell=True, trailing colon on node
        args = mock_run.call_args[0][0]
        assert args[1:] == ["file", "cp", str(f), "iphone:"]
        svc.close()

    def test_tailscale_timeout(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ts", timeout=300)):
            r = svc.send_file(f, "iphone")
        assert not r.success
        assert "timed out" in r.error
        svc.close()

    def test_subprocess_exception(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc()
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", side_effect=OSError("permission denied")):
            r = svc.send_file(f, "iphone")
        assert not r.success
        assert "permission denied" in r.error
        svc.close()


# ─────────────────────────────────────────────────────────────────────────────
# list_nodes
# ─────────────────────────────────────────────────────────────────────────────

class TestListNodes:
    def test_no_tailscale_cli(self):
        svc = _make_svc()
        with patch("shutil.which", return_value=None):
            nodes = svc.list_nodes()
        assert nodes == []
        svc.close()

    def test_cli_nonzero(self):
        svc = _make_svc()
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=mock_result):
            nodes = svc.list_nodes()
        assert nodes == []
        svc.close()

    def test_parses_online_peers(self):
        svc = _make_svc()
        status_json = json.dumps({
            "Peer": {
                "aaa": {"Online": True,  "HostName": "iphone", "TailscaleIPs": ["100.64.0.2"]},
                "bbb": {"Online": False, "HostName": "macbook", "TailscaleIPs": ["100.64.0.3"]},
                "ccc": {"Online": True,  "HostName": "ipad",   "TailscaleIPs": ["100.64.0.4"]},
            }
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = status_json
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=mock_result):
            nodes = svc.list_nodes()
        # offline macbook excluded; sorted alphabetically
        assert nodes == ["ipad", "iphone"]
        svc.close()

    def test_fallback_to_ip_when_no_hostname(self):
        svc = _make_svc()
        status_json = json.dumps({
            "Peer": {
                "aaa": {"Online": True, "HostName": "", "TailscaleIPs": ["100.64.0.5"]},
            }
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = status_json
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=mock_result):
            nodes = svc.list_nodes()
        assert nodes == ["100.64.0.5"]
        svc.close()


# ─────────────────────────────────────────────────────────────────────────────
# on_download_completed
# ─────────────────────────────────────────────────────────────────────────────

class TestOnDownloadCompleted:
    def test_disabled_does_nothing(self, tmp_path):
        svc = _make_svc(enabled=False)
        task = _make_task(str(tmp_path / "file.mp4"))
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_not_called()
        svc.close()

    def test_no_target_node_skips(self, tmp_path):
        cfg = _make_config(enabled=True, node="")
        svc = TaildropService(config=cfg, event_bus=_make_bus())
        task = _make_task(str(tmp_path / "file.mp4"))
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_not_called()
        svc.close()

    def test_missing_output_path_skips(self):
        svc = _make_svc(enabled=True, node="iphone")
        task = _make_task(output_path=None)
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_not_called()
        svc.close()

    def test_filename_field_triggers_transfer(self, tmp_path):
        """Regression: DownloadTask uses .filename (not .output_path).

        yt_dlp_engine sets task.filename via the pp_hook after the merge
        step completes.  TaildropService must read that field; relying only
        on the non-existent .output_path/.file_path aliases caused every
        real download to log 'output_path missing or file not found (None)'
        and silently skip the Taildrop transfer.
        """
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone")
        # Simulate a real DownloadTask: filename is set, output_path is absent/None
        task = _make_task(filename=str(f), output_path=None)
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_called_once()
        svc.close()

    def test_nonexistent_file_skips(self, tmp_path):
        svc = _make_svc(enabled=True, node="iphone")
        task = _make_task(str(tmp_path / "ghost.mp4"))  # doesn't exist
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_not_called()
        svc.close()

    def test_valid_file_submits_to_executor(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone")
        task = _make_task(str(f))
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(task)
        mock_submit.assert_called_once()
        svc.close()

    def test_closed_service_does_not_submit(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        svc = _make_svc(enabled=True, node="iphone")
        svc.close()  # close BEFORE calling
        # After close, executor is shut down — submit must not be called
        with patch.object(svc._executor, "submit") as mock_submit:
            svc.on_download_completed(_make_task(str(f)))
        mock_submit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Event publishing via _transfer
# ─────────────────────────────────────────────────────────────────────────────

class TestTransferEvents:
    def test_success_publishes_completed_event(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        bus = _make_bus()
        svc = TaildropService(config=_make_config(True, "iphone"), event_bus=bus)

        ok_result = MagicMock()
        ok_result.returncode = 0
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok_result):
            svc._transfer(_make_task(str(f)), f, "iphone")

        bus.publish_taildrop_completed.assert_called_once()
        bus.publish_taildrop_failed.assert_not_called()
        svc.close()

    def test_failure_publishes_failed_event(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        bus = _make_bus()
        svc = TaildropService(config=_make_config(True, "iphone"), event_bus=bus)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "peer offline"
        fail_result.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=fail_result):
            svc._transfer(_make_task(str(f)), f, "iphone")

        bus.publish_taildrop_failed.assert_called_once()
        bus.publish_taildrop_completed.assert_not_called()
        svc.close()


# ─────────────────────────────────────────────────────────────────────────────
# ConfigManager typed properties
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigManagerTaildropProperties:
    """Test the 3 new typed properties added to ConfigManager."""

    def _make_real_config(self, tmp_path, overrides=None):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "config.json"
        cfg = ConfigManager(p)
        if overrides:
            for k, v in overrides.items():
                cfg.set(k, v)
        return cfg

    def test_defaults(self, tmp_path):
        cfg = self._make_real_config(tmp_path)
        assert cfg.taildrop_enabled is False
        assert cfg.taildrop_target_node == ""
        assert cfg.taildrop_send_mode == "ask"  # _DEFAULTS["taildrop_send_mode"] = "ask"

    def test_set_enabled(self, tmp_path):
        cfg = self._make_real_config(tmp_path, {"taildrop_enabled": True})
        assert cfg.taildrop_enabled is True

    def test_set_node(self, tmp_path):
        cfg = self._make_real_config(tmp_path, {"taildrop_target_node": "iphone"})
        assert cfg.taildrop_target_node == "iphone"

    def test_node_strips_whitespace(self, tmp_path):
        cfg = self._make_real_config(tmp_path, {"taildrop_target_node": "  iphone  "})
        assert cfg.taildrop_target_node == "iphone"

    def test_invalid_send_mode_falls_back(self, tmp_path):
        cfg = self._make_real_config(tmp_path, {"taildrop_send_mode": "invalid_value"})
        assert cfg.taildrop_send_mode == "always"

    def test_is_tailscale_available_true(self, tmp_path):
        svc = _make_svc()
        with patch("shutil.which", return_value="/usr/bin/tailscale"):
            assert svc.is_tailscale_available() is True
        svc.close()

    def test_is_tailscale_available_false(self, tmp_path):
        svc = _make_svc()
        with patch("shutil.which", return_value=None):
            assert svc.is_tailscale_available() is False
        svc.close()


# ─────────────────────────────────────────────────────────────────────────────
# send_converted_file() — convert pipeline hook
# ─────────────────────────────────────────────────────────────────────────────

class TestSendConvertedFile:
    """
    TaildropService.send_converted_file() submits a background transfer for a
    converted MP4, using the same executor and _do_send() as the download path.

    All tests drive the executor synchronously by letting the submitted thread
    finish before the assertions run (join via svc.close()).
    """

    # ── Guards — early returns that must NOT call subprocess ─────────────

    def test_disabled_does_nothing(self, tmp_path):
        """taildrop_enabled=False → no subprocess, no bus event."""
        svc = _make_svc(enabled=False, node="iphone")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        with patch("subprocess.run") as mock_run:
            svc.send_converted_file(f)
            svc.close()

        mock_run.assert_not_called()
        svc._bus.publish_convert_taildrop_completed.assert_not_called()
        svc._bus.publish_convert_taildrop_failed.assert_not_called()

    def test_no_node_does_nothing(self, tmp_path):
        """taildrop_target_node='' → no subprocess, no bus event."""
        svc = _make_svc(enabled=True, node="")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        with patch("subprocess.run") as mock_run:
            svc.send_converted_file(f)
            svc.close()

        mock_run.assert_not_called()
        svc._bus.publish_convert_taildrop_completed.assert_not_called()

    def test_missing_file_does_nothing(self, tmp_path):
        """File does not exist → skip silently, no subprocess call."""
        svc = _make_svc(enabled=True, node="iphone")
        ghost = tmp_path / "ghost.mp4"  # not created on disk

        with patch("subprocess.run") as mock_run:
            svc.send_converted_file(ghost)
            svc.close()

        mock_run.assert_not_called()
        svc._bus.publish_convert_taildrop_completed.assert_not_called()

    def test_closed_service_does_nothing(self, tmp_path):
        """Service already closed → skip, no subprocess call."""
        svc = _make_svc(enabled=True, node="iphone")
        svc.close()  # close before calling

        f = tmp_path / "out.mp4"
        f.write_bytes(b"x")

        with patch("subprocess.run") as mock_run:
            svc.send_converted_file(f)

        mock_run.assert_not_called()
        svc._bus.publish_convert_taildrop_completed.assert_not_called()

    # ── Success path ─────────────────────────────────────────────────────

    def test_success_publishes_completed_event(self, tmp_path):
        """Successful tailscale send → CONVERT_TAILDROP_COMPLETED on bus."""
        svc = _make_svc(enabled=True, node="iphone")
        f = tmp_path / "video_iPhone.mp4"
        f.write_bytes(b"fake-mp4-data")

        ok = MagicMock()
        ok.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok):
            svc.send_converted_file(f)
            svc.close()  # blocks until worker finishes

        svc._bus.publish_convert_taildrop_completed.assert_called_once_with(
            out_path=f, dest_node="iphone"
        )
        svc._bus.publish_convert_taildrop_failed.assert_not_called()

    def test_success_calls_correct_tailscale_command(self, tmp_path):
        """Verifies the subprocess args: tailscale file cp <path> <node>:"""
        svc = _make_svc(enabled=True, node="my-iphone")
        f = tmp_path / "video_iPhone.mp4"
        f.write_bytes(b"data")

        ok = MagicMock()
        ok.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/tailscale") as mock_which, \
             patch("subprocess.run", return_value=ok) as mock_run:
            svc.send_converted_file(f)
            svc.close()

        mock_run.assert_called_once_with(
            ["/usr/bin/tailscale", "file", "cp", str(f), "my-iphone:"],
            capture_output=True,
            text=True,
            timeout=300,
        )

    # ── Failure paths ────────────────────────────────────────────────────

    def test_tailscale_not_found_publishes_failed_event(self, tmp_path):
        """tailscale CLI not on PATH → CONVERT_TAILDROP_FAILED on bus."""
        svc = _make_svc(enabled=True, node="iphone")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"data")

        with patch("shutil.which", return_value=None):
            svc.send_converted_file(f)
            svc.close()

        call_kwargs = svc._bus.publish_convert_taildrop_failed.call_args
        assert call_kwargs is not None
        assert "not found" in call_kwargs.kwargs["error"].lower()
        svc._bus.publish_convert_taildrop_completed.assert_not_called()

    def test_tailscale_nonzero_exit_publishes_failed_event(self, tmp_path):
        """tailscale exits non-zero → CONVERT_TAILDROP_FAILED with stderr."""
        svc = _make_svc(enabled=True, node="iphone")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"data")

        fail = MagicMock()
        fail.returncode = 1
        fail.stderr = "peer unreachable"
        fail.stdout = ""

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=fail):
            svc.send_converted_file(f)
            svc.close()

        call_kwargs = svc._bus.publish_convert_taildrop_failed.call_args
        assert call_kwargs is not None
        assert "peer unreachable" in call_kwargs.kwargs["error"]
        svc._bus.publish_convert_taildrop_completed.assert_not_called()

    def test_transfer_timeout_publishes_failed_event(self, tmp_path):
        """subprocess.TimeoutExpired → CONVERT_TAILDROP_FAILED."""
        svc = _make_svc(enabled=True, node="iphone")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"data")

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="tailscale", timeout=300)):
            svc.send_converted_file(f)
            svc.close()

        call_kwargs = svc._bus.publish_convert_taildrop_failed.call_args
        assert call_kwargs is not None
        assert "timed out" in call_kwargs.kwargs["error"].lower()

    # ── Isolation from download pipeline ─────────────────────────────────

    def test_does_not_call_download_taildrop_events(self, tmp_path):
        """send_converted_file must NEVER emit TAILDROP_COMPLETED/FAILED
        (download-pipeline events) — only the convert-taildrop variants."""
        svc = _make_svc(enabled=True, node="iphone")
        f = tmp_path / "out.mp4"
        f.write_bytes(b"data")

        ok = MagicMock()
        ok.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=ok):
            svc.send_converted_file(f)
            svc.close()

        # Download-pipeline methods must remain untouched
        svc._bus.publish_taildrop_completed.assert_not_called()
        svc._bus.publish_taildrop_failed.assert_not_called()
