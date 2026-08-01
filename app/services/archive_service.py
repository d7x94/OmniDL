"""
app/services/archive_service.py
Compress files into password-protected .zip/.7z archives and extract them safely.

Design constraints
──────────────────
• .zip and .7z only. RAR is out of scope — no open-source library can write
  the proprietary RAR format.
• .7z supports real header encryption (filenames hidden) via
  header_encryption=True. ZIP cannot encrypt filenames — the central
  directory is always plaintext even with AES content encryption. This is a
  format limitation, not a missing feature: encrypt_header=True with
  fmt="zip" raises ValueError rather than silently ignoring the request.
• Extraction validates every archive member's path against the destination
  before writing anything (zip-slip / 7z-slip guard) and enforces a total
  uncompressed-size / ratio / member-count cap (decompression-bomb guard).
  Extraction stages into a temp directory first and is only copied into the
  caller's destination on full success, so a rejected archive never leaves
  partial output behind and never touches pre-existing files in dest_dir.
• Stateless across calls — no job registry, no shared mutable state, so no
  lock is needed. Both compress() and extract() are blocking/synchronous;
  callers run them off their own thread (UI: threading.Thread, API:
  asyncio.to_thread).
• Password is bytes at this boundary and is never logged.
"""

from __future__ import annotations

import logging
import lzma
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

import py7zr
import pyzipper
from py7zr.exceptions import Bad7zFile, DecompressionBombError, PasswordRequired

logger = logging.getLogger(__name__)

_VALID_FORMATS: frozenset[str] = frozenset({"zip", "7z"})

# Decompression-bomb caps. Not exposed as tunable params — nothing in the
# requirements asked for that, and fixed limits are simpler to reason about.
_MAX_TOTAL_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
_MAX_RATIO = 200  # declared-uncompressed / on-disk-compressed
_MAX_MEMBER_COUNT = 100_000
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"

# Wrong/garbled 7z passwords surface as a grab-bag of low-level decode
# failures rather than one clean exception (verified against py7zr 1.1.3).
# By the time these fire, member paths have already been validated by
# _safe_member_path()/is_symlink checks below, so a failure here is a
# password problem, not a traversal problem.
_PY7ZR_PASSWORD_ERRORS = (
    PasswordRequired,
    Bad7zFile,
    TypeError,
    EOFError,
    UnicodeDecodeError,
    lzma.LZMAError,
)


class ArchiveError(Exception):
    """Base exception for archive operations."""


class ArchivePasswordError(ArchiveError):
    """Missing or incorrect password."""


class ArchivePathTraversalError(ArchiveError):
    """An archive member would write outside the destination directory."""


class ArchiveBombError(ArchiveError):
    """Archive exceeds the size/ratio/member-count safety limits."""


@dataclass
class ArchiveMember:
    name: str
    size: int
    compressed_size: int
    is_dir: bool


@dataclass
class ExtractResult:
    extracted_paths: list[Path]
    total_bytes: int


@dataclass
class _RawExtract:
    extracted_paths: list[Path]
    total_bytes: int


def _safe_member_path(name: str, dest_root: Path) -> Path:
    """Resolve an archive member name against dest_root, rejecting any escape attempt."""
    if not name or "\x00" in name:
        raise ArchivePathTraversalError(f"Archive member has an invalid name: {name!r}")
    posix_name = name.replace("\\", "/")
    if posix_name.startswith("//") or (len(posix_name) >= 2 and posix_name[1] == ":"):
        raise ArchivePathTraversalError(f"Archive member path escapes destination: {name!r}")
    parts = PurePosixPath(posix_name).parts
    if not parts or PurePosixPath(posix_name).is_absolute() or ".." in parts:
        raise ArchivePathTraversalError(f"Archive member path escapes destination: {name!r}")

    resolved_root = dest_root.resolve(strict=False)
    candidate = (resolved_root / posix_name).resolve(strict=False)
    if not candidate.is_relative_to(resolved_root):
        raise ArchivePathTraversalError(f"Archive member path escapes destination: {name!r}")
    return candidate


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _preflight_bomb_check(member_count: int, declared_uncompressed: int, archive_size_on_disk: int) -> None:
    if member_count > _MAX_MEMBER_COUNT:
        raise ArchiveBombError(f"Archive has {member_count} members, exceeds limit of {_MAX_MEMBER_COUNT}")
    if declared_uncompressed > _MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ArchiveBombError(
            f"Archive declares {declared_uncompressed} uncompressed bytes, "
            f"exceeds limit of {_MAX_TOTAL_UNCOMPRESSED_BYTES}"
        )
    if archive_size_on_disk and declared_uncompressed / archive_size_on_disk > _MAX_RATIO:
        raise ArchiveBombError(
            f"Archive compression ratio {declared_uncompressed / archive_size_on_disk:.0f}x "
            f"exceeds limit of {_MAX_RATIO}x"
        )


def _check_disk_space(stage_dir: Path, declared_uncompressed: int) -> None:
    free = shutil.disk_usage(stage_dir).free
    if declared_uncompressed > free:
        raise ArchiveBombError(
            f"Archive needs {declared_uncompressed} bytes to extract, "
            f"only {free} bytes free on the staging filesystem"
        )


def _sniff_format(archive_path: Path) -> str:
    with archive_path.open("rb") as fh:
        head = fh.read(6)
    if head.startswith(_7Z_MAGIC):
        return "7z"
    if head[:2] == b"PK":
        return "zip"
    raise ValueError(f"Cannot determine archive format for {archive_path.name!r}; pass fmt explicitly")


def _iter_real_files(root: Path) -> list[Path]:
    """Files under root, skipping symlinks so a nested link can't smuggle
    content from outside the source tree into the archive."""
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _add_source_to_zip(zf: pyzipper.AESZipFile, source: Path) -> None:
    if source.is_file():
        zf.write(source, arcname=source.name)
    else:
        for f in _iter_real_files(source):
            zf.write(f, arcname=str(Path(source.name) / f.relative_to(source)))


def _write_archive(
    sources: list[Path], archive_path: Path, fmt: str, password: Optional[bytes], encrypt_header: bool
) -> None:
    if fmt == "7z":
        try:
            pw = password.decode("utf-8") if password else None
        except UnicodeDecodeError as exc:
            raise ArchiveError("Password must be valid UTF-8") from exc
        with py7zr.SevenZipFile(archive_path, "w", password=pw, header_encryption=encrypt_header) as archive:
            for src in sources:
                archive.writeall(src, arcname=src.name)
    else:
        encryption = pyzipper.WZ_AES if password else None
        with pyzipper.AESZipFile(
            archive_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=encryption
        ) as zf:
            if password:
                zf.setpassword(password)
            for src in sources:
                _add_source_to_zip(zf, src)


def _check_extract_destination(stage_dir: Path, dest_dir: Path) -> None:
    """Refuse to extract if dest_dir isn't a directory, or if any staged file
    would overwrite an existing file there — extends the "rejected extraction
    never touches dest_dir" guarantee to collisions, not just failures."""
    if dest_dir.exists() and not dest_dir.is_dir():
        raise ArchiveError(f"Destination exists and is not a directory: {dest_dir}")
    if not dest_dir.is_dir():
        return
    conflicts = sorted(
        str(p.relative_to(stage_dir))
        for p in stage_dir.rglob("*")
        if p.is_file() and (dest_dir / p.relative_to(stage_dir)).exists()
    )
    if conflicts:
        shown = ", ".join(conflicts[:5])
        more = f" and {len(conflicts) - 5} more" if len(conflicts) > 5 else ""
        raise ArchiveError(f"Extraction would overwrite existing file(s): {shown}{more}")


class ArchiveService:
    """Compress files into password-protected .zip/.7z archives and extract them safely."""

    def compress(
        self,
        sources: list[Path],
        output_dir: Path,
        fmt: str,
        *,
        archive_name: str = "archive",
        password: Optional[bytes] = None,
        encrypt_header: bool = False,
        individually: bool = False,
        on_progress: Optional[Callable[[float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[Path]:
        """
        Compress sources into one archive, or one archive per source when
        individually=True. Returns the list of created archive paths.

        Raises ValueError for invalid parameters — callers convert to HTTP 422.
        """
        if fmt not in _VALID_FORMATS:
            raise ValueError(f"fmt '{fmt}' is not allowed. Valid values: {sorted(_VALID_FORMATS)}")
        if encrypt_header and fmt != "7z":
            raise ValueError("encrypt_header is only supported for fmt='7z' — ZIP cannot encrypt filenames")
        if not sources:
            raise ValueError("sources must not be empty")
        if any(sep in archive_name for sep in ("/", "\\")) or archive_name in ("", ".", ".."):
            raise ValueError(f"archive_name {archive_name!r} is not a valid filename")

        output_dir.mkdir(parents=True, exist_ok=True)

        if individually:
            expanded: list[Path] = []
            for src in sources:
                if src.is_dir():
                    expanded.extend(_iter_real_files(src))
                else:
                    expanded.append(src)
            if not expanded:
                raise ValueError("No files found under the given directory sources")
            sources = expanded
            results: list[Path] = []
            seen_stems: dict[str, int] = {}
            for src in sources:
                if cancel_event is not None and cancel_event.is_set():
                    raise ArchiveError("Compression cancelled")
                count = seen_stems.get(src.stem, 0)
                seen_stems[src.stem] = count + 1
                stem = src.stem if count == 0 else f"{src.stem}_{count}"
                archive_path = output_dir / f"{stem}.{fmt}"
                if archive_path.exists():
                    raise ArchiveError(f"Archive already exists: {archive_path}")
                _write_archive([src], archive_path, fmt, password, encrypt_header)
                results.append(archive_path)
                if on_progress:
                    on_progress(100.0 * len(results) / len(sources))
            return results

        if cancel_event is not None and cancel_event.is_set():
            raise ArchiveError("Compression cancelled")
        archive_path = output_dir / f"{archive_name}.{fmt}"
        if archive_path.exists():
            raise ArchiveError(f"Archive already exists: {archive_path}")
        _write_archive(sources, archive_path, fmt, password, encrypt_header)
        if on_progress:
            on_progress(100.0)
        return [archive_path]

    def extract(
        self,
        archive_path: Path,
        dest_dir: Path,
        *,
        fmt: Optional[str] = None,
        password: Optional[bytes] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> ExtractResult:
        """
        Extract archive_path into dest_dir.

        Every member is validated (path-traversal + symlink rejection) before
        anything is written, and the result is staged in a temp directory so
        a rejected extraction leaves dest_dir untouched.
        """
        if not archive_path.is_file():
            raise FileNotFoundError(str(archive_path))
        resolved_fmt = fmt or _sniff_format(archive_path)
        if resolved_fmt not in _VALID_FORMATS:
            raise ValueError(f"fmt '{resolved_fmt}' is not allowed. Valid values: {sorted(_VALID_FORMATS)}")
        if cancel_event is not None and cancel_event.is_set():
            raise ArchiveError("Extraction cancelled")

        stage_dir = Path(tempfile.mkdtemp(prefix="omnidl-archive-extract-"))
        try:
            if resolved_fmt == "7z":
                raw = self._extract_7z(archive_path, stage_dir, password)
            else:
                raw = self._extract_zip(archive_path, stage_dir, password)
            _check_extract_destination(stage_dir, dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage_dir, dest_dir, dirs_exist_ok=True)
            extracted_paths = [dest_dir / p.relative_to(stage_dir) for p in raw.extracted_paths]
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

        if on_progress:
            on_progress(100.0)
        return ExtractResult(extracted_paths=extracted_paths, total_bytes=raw.total_bytes)

    def list_contents(
        self, archive_path: Path, *, fmt: Optional[str] = None, password: Optional[bytes] = None
    ) -> list[ArchiveMember]:
        """
        List archive members without extracting.

        For header-encrypted .7z, names themselves require the password and
        this raises ArchivePasswordError if it's missing/wrong. For .zip,
        names are always readable without a password — content encryption
        does not hide filenames, which is the documented ZIP limitation.
        """
        if not archive_path.is_file():
            raise FileNotFoundError(str(archive_path))
        resolved_fmt = fmt or _sniff_format(archive_path)
        if resolved_fmt not in _VALID_FORMATS:
            raise ValueError(f"fmt '{resolved_fmt}' is not allowed. Valid values: {sorted(_VALID_FORMATS)}")

        if resolved_fmt == "7z":
            pw = password.decode("utf-8") if password else None
            try:
                with py7zr.SevenZipFile(archive_path, "r", password=pw) as archive:
                    infos = archive.list()
            except _PY7ZR_PASSWORD_ERRORS as exc:
                raise ArchivePasswordError("Incorrect or missing password") from exc
            return [
                ArchiveMember(
                    name=f.filename,
                    size=f.uncompressed,
                    compressed_size=f.compressed or 0,
                    is_dir=f.is_directory,
                )
                for f in infos
            ]

        try:
            with pyzipper.AESZipFile(archive_path, "r") as zf:
                infos = zf.infolist()
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip file: {archive_path.name}") from exc
        return [
            ArchiveMember(
                name=i.filename, size=i.file_size, compressed_size=i.compress_size, is_dir=i.is_dir()
            )
            for i in infos
        ]

    # ── Internal ─────────────────────────────────────────────────────────

    def _extract_7z(self, archive_path: Path, stage_dir: Path, password: Optional[bytes]) -> _RawExtract:
        pw = password.decode("utf-8") if password else None
        try:
            with py7zr.SevenZipFile(archive_path, "r", password=pw) as archive:
                infos = archive.list()
        except _PY7ZR_PASSWORD_ERRORS as exc:
            raise ArchivePasswordError("Incorrect or missing password") from exc

        archive_size = archive_path.stat().st_size
        file_infos = [f for f in infos if not f.is_directory]
        declared_uncompressed = sum(f.uncompressed for f in file_infos)
        _preflight_bomb_check(len(infos), declared_uncompressed, archive_size)
        _check_disk_space(stage_dir, declared_uncompressed)

        extracted_paths: list[Path] = []
        for f in infos:
            if f.is_symlink:
                raise ArchivePathTraversalError(f"Archive contains a symlink member: {f.filename!r}")
            safe_path = _safe_member_path(f.filename, stage_dir)
            if not f.is_directory:
                extracted_paths.append(safe_path)

        # Reopened rather than reused: py7zr's password/max_extract_size are
        # constructor-time settings and the listing pass above already
        # consumed the reader. Archive ops here are single-shot/bounded
        # (see RemoteConvertService design note), so the extra open is cheap.
        try:
            with py7zr.SevenZipFile(
                archive_path, "r", password=pw, max_extract_size=_MAX_TOTAL_UNCOMPRESSED_BYTES
            ) as archive:
                archive.extract(path=stage_dir)
        except DecompressionBombError as exc:
            raise ArchiveBombError(str(exc)) from exc
        except _PY7ZR_PASSWORD_ERRORS as exc:
            raise ArchivePasswordError("Incorrect or missing password") from exc

        total_bytes = sum(f.uncompressed for f in file_infos)
        return _RawExtract(extracted_paths=extracted_paths, total_bytes=total_bytes)

    def _extract_zip(self, archive_path: Path, stage_dir: Path, password: Optional[bytes]) -> _RawExtract:
        try:
            zf = pyzipper.AESZipFile(archive_path, "r")
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Not a valid zip file: {archive_path.name}") from exc

        with zf:
            if password:
                zf.setpassword(password)
            infos = zf.infolist()
            archive_size = archive_path.stat().st_size
            file_infos = [i for i in infos if not i.is_dir()]
            declared_uncompressed = sum(i.file_size for i in file_infos)
            _preflight_bomb_check(len(infos), declared_uncompressed, archive_size)
            _check_disk_space(stage_dir, declared_uncompressed)

            safe_members: list[tuple[zipfile.ZipInfo, Path]] = []
            for info in infos:
                if _is_zip_symlink(info):
                    raise ArchivePathTraversalError(f"Archive contains a symlink member: {info.filename!r}")
                safe_members.append((info, _safe_member_path(info.filename, stage_dir)))

            extracted_paths: list[Path] = []
            total_written = 0
            for info, safe_path in safe_members:
                if info.is_dir():
                    safe_path.mkdir(parents=True, exist_ok=True)
                    continue
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(info, pwd=password) as src, open(safe_path, "wb") as dst:
                        while True:
                            chunk = src.read(_CHUNK_SIZE)
                            if not chunk:
                                break
                            total_written += len(chunk)
                            if total_written > _MAX_TOTAL_UNCOMPRESSED_BYTES or (
                                archive_size and total_written / archive_size > _MAX_RATIO
                            ):
                                raise ArchiveBombError(
                                    f"Extraction aborted: decompressed size exceeds safety limit "
                                    f"({total_written} bytes)"
                                )
                            dst.write(chunk)
                except RuntimeError as exc:
                    raise ArchivePasswordError("Incorrect or missing password") from exc
                extracted_paths.append(safe_path)

        return _RawExtract(extracted_paths=extracted_paths, total_bytes=total_written)
