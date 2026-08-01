"""
tests/test_archive_service.py
Unit tests for app/services/archive_service.py — ArchiveService.

Coverage targets:
- compress()/extract() round trip for zip and 7z, with and without password
- wrong password -> ArchivePasswordError, no partial output
- encrypt_header=True + fmt="zip" -> ValueError
- 7z header encryption gates list_contents(); zip names always readable
- zip-slip / 7z-slip / symlink member rejection -> ArchivePathTraversalError
- decompression-bomb rejection -> ArchiveBombError
- individually=True mode, including stem-collision suffixing
- _safe_member_path() traversal guard, unit-tested directly

All tests use real files under tmp_path — no mocking, this module is pure I/O.
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import py7zr
import pytest

from app.services.archive_service import (
    ArchiveBombError,
    ArchiveError,
    ArchivePasswordError,
    ArchivePathTraversalError,
    ArchiveService,
    _safe_member_path,
)

pytestmark = pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="symlink support required for traversal tests"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_src_dir(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello a")
    (src / "b.txt").write_text("hello b")
    return src


def _make_service() -> ArchiveService:
    return ArchiveService()


# ---------------------------------------------------------------------------
# compress() validation
# ---------------------------------------------------------------------------


class TestCompressValidation:
    def test_invalid_fmt_raises(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        with pytest.raises(ValueError):
            svc.compress([src], tmp_path / "out", "rar")

    def test_empty_sources_raises(self, tmp_path: Path) -> None:
        svc = _make_service()
        with pytest.raises(ValueError):
            svc.compress([], tmp_path / "out", "zip")

    def test_encrypt_header_on_zip_raises(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        with pytest.raises(ValueError):
            svc.compress([src], tmp_path / "out", "zip", encrypt_header=True)

    def test_invalid_archive_name_raises(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        with pytest.raises(ValueError):
            svc.compress([src], tmp_path / "out", "zip", archive_name="../evil")

    def test_individually_with_empty_directory_source_raises(self, tmp_path: Path) -> None:
        svc = _make_service()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(ValueError):
            svc.compress([empty_dir], tmp_path / "out", "zip", individually=True)

    def test_compress_rejects_existing_archive_name(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")
        original_content = archive.read_bytes()

        with pytest.raises(ArchiveError):
            svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")
        assert archive.read_bytes() == original_content

    def test_compress_individually_rejects_existing_archive_name(self, tmp_path: Path) -> None:
        svc = _make_service()
        f = tmp_path / "one.txt"
        f.write_text("1")
        svc.compress([f], tmp_path / "out", "zip", individually=True)

        with pytest.raises(ArchiveError):
            svc.compress([f], tmp_path / "out", "zip", individually=True)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.parametrize("fmt", ["zip", "7z"])
    @pytest.mark.parametrize("password", [None, b"secret"])
    def test_compress_extract_round_trip(self, tmp_path: Path, fmt: str, password: bytes | None) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", fmt, archive_name="bundle", password=password)
        assert archive.exists()

        result = svc.extract(archive, tmp_path / "extracted", password=password)
        assert (tmp_path / "extracted" / "src" / "a.txt").read_text() == "hello a"
        assert (tmp_path / "extracted" / "src" / "b.txt").read_text() == "hello b"
        assert result.total_bytes > 0

    def test_fmt_sniffed_when_not_given(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", "7z", archive_name="bundle")
        result = svc.extract(archive, tmp_path / "extracted")
        assert result.extracted_paths


# ---------------------------------------------------------------------------
# Password errors
# ---------------------------------------------------------------------------


class TestPasswordErrors:
    @pytest.mark.parametrize("fmt", ["zip", "7z"])
    def test_wrong_password_rejected_no_partial_output(self, tmp_path: Path, fmt: str) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", fmt, archive_name="bundle", password=b"right")

        dest = tmp_path / "extracted"
        with pytest.raises(ArchivePasswordError):
            svc.extract(archive, dest, password=b"wrong")
        assert not dest.exists()

    def test_7z_compress_invalid_utf8_password_raises_archive_error(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        with pytest.raises(ArchiveError):
            svc.compress([src], tmp_path / "out", "7z", archive_name="bundle", password=b"\xff\xfe")

    def test_7z_header_encrypted_listing_requires_password(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress(
            [src], tmp_path / "out", "7z", archive_name="bundle", password=b"secret", encrypt_header=True
        )
        with pytest.raises(ArchivePasswordError):
            svc.list_contents(archive)
        members = svc.list_contents(archive, password=b"secret")
        assert any(m.name.endswith("a.txt") for m in members)

    def test_zip_listing_never_requires_password(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle", password=b"secret")
        members = svc.list_contents(archive)  # no password
        assert any(m.name.endswith("a.txt") for m in members)
        # content still requires it
        with pytest.raises(ArchivePasswordError):
            svc.extract(archive, tmp_path / "extracted")


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../evil.txt", "pwned")

        svc = _make_service()
        dest = tmp_path / "extracted"
        with pytest.raises(ArchivePathTraversalError):
            svc.extract(evil, dest)
        assert not dest.exists()
        assert not (tmp_path / "evil.txt").exists()

    def test_zip_absolute_path_rejected(self, tmp_path: Path) -> None:
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("/etc/passwd", "pwned")

        svc = _make_service()
        dest = tmp_path / "extracted"
        with pytest.raises(ArchivePathTraversalError):
            svc.extract(evil, dest)
        assert not dest.exists()

    def test_7z_symlink_member_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        src = tmp_path / "src"
        src.mkdir()
        (src / "link").symlink_to(outside)

        arc = tmp_path / "evil.7z"
        with py7zr.SevenZipFile(arc, "w") as z:
            z.writeall(src, arcname="src")

        svc = _make_service()
        dest = tmp_path / "extracted"
        with pytest.raises(ArchivePathTraversalError):
            svc.extract(arc, dest)
        assert not dest.exists()

    def test_zip_symlink_member_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")

        arc = tmp_path / "evil.zip"
        info = zipfile.ZipInfo("src/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(arc, "w") as zf:
            zf.writestr(info, str(outside))

        svc = _make_service()
        dest = tmp_path / "extracted"
        with pytest.raises(ArchivePathTraversalError):
            svc.extract(arc, dest)
        assert not dest.exists()


# ---------------------------------------------------------------------------
# Compress-side symlink safety (nested symlinks must not be dereferenced)
# ---------------------------------------------------------------------------


class TestCompressSymlinkSafety:
    def test_zip_compress_skips_nested_symlink_to_outside_file(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside_secret.txt"
        outside.write_text("TOP_SECRET_CONTENT")
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello a")
        (src / "link.txt").symlink_to(outside)

        svc = _make_service()
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            for name in names:
                assert b"TOP_SECRET_CONTENT" not in zf.read(name)
        assert any(n.endswith("a.txt") for n in names)
        assert not any(n.endswith("link.txt") for n in names)

    def test_individually_mode_skips_nested_symlink_to_outside_file(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside_secret.txt"
        outside.write_text("TOP_SECRET_CONTENT")
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello a")
        (src / "link.txt").symlink_to(outside)

        svc = _make_service()
        archives = svc.compress([src], tmp_path / "out", "zip", individually=True)

        names = sorted(a.name for a in archives)
        assert names == ["a.zip"]
        with zipfile.ZipFile(archives[0]) as zf:
            for name in zf.namelist():
                assert b"TOP_SECRET_CONTENT" not in zf.read(name)


# ---------------------------------------------------------------------------
# Decompression bomb
# ---------------------------------------------------------------------------


class TestDecompressionBomb:
    @pytest.mark.parametrize("fmt", ["zip", "7z"])
    def test_bomb_rejected_no_leftover_files(
        self, tmp_path: Path, fmt: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.archive_service as archive_module

        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", fmt, archive_name="bundle")

        monkeypatch.setattr(archive_module, "_MAX_TOTAL_UNCOMPRESSED_BYTES", 3)

        dest = tmp_path / "extracted"
        with pytest.raises(ArchiveBombError):
            svc.extract(archive, dest)
        assert not dest.exists()

    @pytest.mark.parametrize("fmt", ["zip", "7z"])
    def test_extract_rejected_when_insufficient_disk_space(
        self, tmp_path: Path, fmt: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil as shutil_module

        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", fmt, archive_name="bundle")

        fake_usage = shutil_module.disk_usage(tmp_path)._replace(free=0)
        monkeypatch.setattr(shutil_module, "disk_usage", lambda path: fake_usage)
        dest = tmp_path / "extracted"
        with pytest.raises(ArchiveBombError):
            svc.extract(archive, dest)
        assert not dest.exists()

    def test_ratio_bomb_rejected_with_real_high_ratio_archive(self, tmp_path: Path) -> None:
        # 5 MiB of zero bytes deflates to a tiny fraction of that size, so this
        # trips the real _MAX_RATIO (200x) check with no monkeypatching.
        svc = _make_service()
        src = tmp_path / "src"
        src.mkdir()
        (src / "zeros.bin").write_bytes(bytes(5 * 1024 * 1024))
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")

        dest = tmp_path / "extracted"
        with pytest.raises(ArchiveBombError):
            svc.extract(archive, dest)
        assert not dest.exists()

    def test_member_count_bomb_rejected_with_real_many_member_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.archive_service as archive_module

        monkeypatch.setattr(archive_module, "_MAX_MEMBER_COUNT", 5)

        svc = _make_service()
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            (src / f"f{i}.txt").write_text(str(i))
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")

        dest = tmp_path / "extracted"
        with pytest.raises(ArchiveBombError):
            svc.extract(archive, dest)
        assert not dest.exists()


# ---------------------------------------------------------------------------
# Extract destination safety (no silent overwrite)
# ---------------------------------------------------------------------------


class TestExtractDestinationSafety:
    def test_extract_rejects_dest_dir_that_is_a_file(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")

        dest = tmp_path / "dest_is_a_file"
        dest.write_text("not a directory")

        with pytest.raises(ArchiveError):
            svc.extract(archive, dest)
        assert dest.read_text() == "not a directory"

    def test_extract_rejects_file_collision_and_leaves_existing_file_untouched(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)
        [archive] = svc.compress([src], tmp_path / "out", "zip", archive_name="bundle")

        dest = tmp_path / "extracted"
        colliding = dest / "src" / "a.txt"
        colliding.parent.mkdir(parents=True)
        colliding.write_text("pre-existing content")

        with pytest.raises(ArchiveError):
            svc.extract(archive, dest)
        assert colliding.read_text() == "pre-existing content"


# ---------------------------------------------------------------------------
# individually mode
# ---------------------------------------------------------------------------


class TestIndividuallyMode:
    def test_three_sources_three_archives(self, tmp_path: Path) -> None:
        svc = _make_service()
        f1 = tmp_path / "one.txt"
        f2 = tmp_path / "two.txt"
        f3 = tmp_path / "three.txt"
        for f, content in ((f1, "1"), (f2, "2"), (f3, "3")):
            f.write_text(content)

        archives = svc.compress([f1, f2, f3], tmp_path / "out", "zip", individually=True)
        assert len(archives) == 3
        for archive, content in zip(archives, ("1", "2", "3")):
            assert archive.exists()
            result = svc.extract(archive, tmp_path / f"x_{archive.stem}")
            assert result.extracted_paths[0].read_text() == content

    def test_stem_collision_suffixed(self, tmp_path: Path) -> None:
        svc = _make_service()
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "x.txt").write_text("from a")
        (dir_b / "x.txt").write_text("from b")

        archives = svc.compress(
            [dir_a / "x.txt", dir_b / "x.txt"], tmp_path / "out", "zip", individually=True
        )
        names = sorted(a.name for a in archives)
        assert names == ["x.zip", "x_1.zip"]

    def test_directory_source_expands_to_one_archive_per_file(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = _make_src_dir(tmp_path)  # a.txt, b.txt

        archives = svc.compress([src], tmp_path / "out", "zip", individually=True)
        names = sorted(a.name for a in archives)
        assert names == ["a.zip", "b.zip"]

    def test_directory_source_expands_recursively(self, tmp_path: Path) -> None:
        svc = _make_service()
        src = tmp_path / "src"
        nested = src / "nested"
        nested.mkdir(parents=True)
        (src / "top.txt").write_text("top")
        (nested / "deep.txt").write_text("deep")

        archives = svc.compress([src], tmp_path / "out", "zip", individually=True)
        names = sorted(a.name for a in archives)
        assert names == ["deep.zip", "top.zip"]


# ---------------------------------------------------------------------------
# _safe_member_path unit tests
# ---------------------------------------------------------------------------


class TestSafeMemberPath:
    def test_valid_relative_path_accepted(self, tmp_path: Path) -> None:
        result = _safe_member_path("sub/file.txt", tmp_path)
        assert result == (tmp_path / "sub" / "file.txt").resolve()

    @pytest.mark.parametrize(
        "name",
        [
            "/etc/passwd",
            "../escape.txt",
            "sub/../../escape.txt",
            "C:/windows/system32",
            "//server/share",
            "",
            "a\x00b",
        ],
    )
    def test_unsafe_names_rejected(self, tmp_path: Path, name: str) -> None:
        with pytest.raises(ArchivePathTraversalError):
            _safe_member_path(name, tmp_path)
