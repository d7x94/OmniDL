"""The isolation guard in this folder must block real I/O before any cookie test relies on it."""

import socket
import subprocess
import sys

import pytest


def test_guard_blocks_socket_connect():
    with pytest.raises(RuntimeError, match="cookies_audit guard"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_guard_blocks_subprocess():
    with pytest.raises(RuntimeError, match="cookies_audit guard"):
        subprocess.run([sys.executable, "-c", "pass"], check=False)


def test_guard_blocks_open_outside_repo_and_tmp():
    with pytest.raises(RuntimeError, match="cookies_audit guard"):
        open("/etc/hostname", encoding="utf-8")


def test_guard_allows_tmp_path(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("ok", encoding="utf-8")
    assert p.read_text(encoding="utf-8") == "ok"
