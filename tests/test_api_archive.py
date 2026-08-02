"""
tests/test_api_archive.py
Route-layer tests for the /api/archive/* endpoints in api/server.py.

Endpoint functions are invoked directly (matching the existing
test_api_sse_cleanup.py convention) rather than through an HTTP TestClient —
this repo has no httpx dependency installed. Auth wiring is verified
structurally (each route depends on _require_auth); path-confinement,
individually-mode validation, and temp-dir cleanup are exercised through
the endpoint bodies directly.
"""

from __future__ import annotations

import asyncio
import inspect
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import api.server as srv
from api.models import ArchiveCompressRequest, ArchiveContentsRequest, ArchiveExtractRequest
from app.services.archive_service import ArchiveService

_EMPTY_ZIP = b"PK\x05\x06" + b"\x00" * 18


def _make_app(download_dir: Path):
    service = SimpleNamespace(get_all_tasks=lambda: [], analyse_url=lambda url, on_done, on_error: None)
    config = SimpleNamespace(api_token="", download_dir=download_dir, taildrop_target_nodes=[])
    return srv.create_app(service, config)  # type: ignore[arg-type]


def _endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route {path} not found")


def _auth_dependency_name(endpoint) -> str:
    dep = inspect.signature(endpoint).parameters["_"].default
    return dep.dependency.__name__


def _expect_status(coro, status_code: int) -> None:
    async def _run():
        with pytest.raises(Exception) as exc_info:
            await coro
        assert exc_info.value.status_code == status_code

    asyncio.run(_run())


class TestAuthWiring:
    @pytest.mark.parametrize(
        "path", ["/api/archive/compress", "/api/archive/extract", "/api/archive/contents"]
    )
    def test_routes_require_auth(self, tmp_path: Path, path: str) -> None:
        app = _make_app(tmp_path)
        endpoint = _endpoint(app, path)
        assert _auth_dependency_name(endpoint) == "_require_auth"


class TestCompressRoute:
    def test_source_outside_download_dir_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body = ArchiveCompressRequest(sources=[str(outside)], fmt="zip")
        _expect_status(endpoint(body, None), 400)

    def test_individually_with_two_sources_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        f1 = download_dir / "a.txt"
        f2 = download_dir / "b.txt"
        f1.write_text("1")
        f2.write_text("2")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body = ArchiveCompressRequest(sources=[str(f1), str(f2)], fmt="zip", individually=True)
        _expect_status(endpoint(body, None), 422)

    def test_encrypt_header_with_zip_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        f = download_dir / "a.txt"
        f.write_text("hello")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body = ArchiveCompressRequest(sources=[str(f)], fmt="zip", encrypt_header=True)
        _expect_status(endpoint(body, None), 422)

    def test_individually_with_directory_source_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src_dir = download_dir / "folder"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("1")
        (src_dir / "b.txt").write_text("2")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body = ArchiveCompressRequest(sources=[str(src_dir)], fmt="zip", individually=True)

        created: list[Path] = []
        orig_mkdtemp = srv.tempfile.mkdtemp

        def _tracking_mkdtemp(*args, **kwargs):
            path = Path(orig_mkdtemp(*args, **kwargs))
            created.append(path)
            return str(path)

        monkeypatch.setattr(srv.tempfile, "mkdtemp", _tracking_mkdtemp)

        _expect_status(endpoint(body, None), 422)
        assert created and not created[0].exists()

    def test_temp_dir_cleaned_up_after_response(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src = download_dir / "a.txt"
        src.write_text("hello")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body = ArchiveCompressRequest(sources=[str(src)], fmt="zip")

        async def _run():
            response = await endpoint(body, None)
            temp_dir = Path(response.path).parent
            assert temp_dir.exists()
            # Starlette runs this after the response body has been sent.
            await response.background()
            assert not temp_dir.exists()

        asyncio.run(_run())


class TestExtractRoute:
    def test_archive_path_outside_download_dir_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        outside = tmp_path / "evil.zip"
        outside.write_bytes(_EMPTY_ZIP)

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(outside))
        _expect_status(endpoint(body, None), 400)

    def test_dest_dir_outside_download_dir_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        archive = download_dir / "a.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a.txt", "hi")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(archive), dest_dir=str(tmp_path / "outside_dest"))
        _expect_status(endpoint(body, None), 400)

    def test_missing_archive_returns_404(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(download_dir / "missing.zip"))
        _expect_status(endpoint(body, None), 404)


class TestContentsRoute:
    def test_archive_path_outside_download_dir_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        outside = tmp_path / "evil.zip"
        outside.write_bytes(_EMPTY_ZIP)

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/contents")
        body = ArchiveContentsRequest(archive_path=str(outside))
        _expect_status(endpoint(body, None), 400)


class TestExtractRouteErrorMapping:
    def test_zip_slip_archive_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        evil = download_dir / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../evil.txt", "pwned")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(evil))
        _expect_status(endpoint(body, None), 400)

    def test_wrong_password_archive_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src = download_dir / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        [archive] = ArchiveService().compress(
            [src], download_dir, "zip", archive_name="bundle", password=b"right"
        )

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(archive), password=SecretStr("wrong"))
        _expect_status(endpoint(body, None), 400)

    def test_bomb_archive_rejected(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src = download_dir / "src"
        src.mkdir()
        # 5 MiB of zero bytes deflates far past the real 200x ratio cap.
        (src / "zeros.bin").write_bytes(bytes(5 * 1024 * 1024))
        [archive] = ArchiveService().compress([src], download_dir, "zip", archive_name="bundle")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(archive))
        _expect_status(endpoint(body, None), 400)


class TestExtractRouteHappyPath:
    def test_extract_returns_expected_response_shape(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src = download_dir / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        [archive] = ArchiveService().compress([src], download_dir, "zip", archive_name="bundle")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/extract")
        body = ArchiveExtractRequest(archive_path=str(archive))

        async def _run():
            response = await endpoint(body, None)
            assert response.total_bytes > 0
            assert response.extracted_paths
            assert response.dest_dir == str(download_dir.resolve() / "extracted" / archive.stem)

        asyncio.run(_run())


class TestContentsRouteHappyPath:
    def test_contents_returns_expected_response_shape(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        src = download_dir / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        [archive] = ArchiveService().compress([src], download_dir, "zip", archive_name="bundle")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/contents")
        body = ArchiveContentsRequest(archive_path=str(archive))

        async def _run():
            response = await endpoint(body, None)
            assert any(m.name.endswith("a.txt") for m in response.members)

        asyncio.run(_run())


class TestArchiveConcurrency:
    def test_concurrent_compress_requests_both_succeed(self, tmp_path: Path) -> None:
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        f1 = download_dir / "one.txt"
        f2 = download_dir / "two.txt"
        f1.write_text("1")
        f2.write_text("2")

        app = _make_app(download_dir)
        endpoint = _endpoint(app, "/api/archive/compress")
        body1 = ArchiveCompressRequest(sources=[str(f1)], fmt="zip")
        body2 = ArchiveCompressRequest(sources=[str(f2)], fmt="zip")

        async def _run():
            r1, r2 = await asyncio.gather(endpoint(body1, None), endpoint(body2, None))
            assert Path(r1.path).exists()
            assert Path(r2.path).exists()

        asyncio.run(_run())
