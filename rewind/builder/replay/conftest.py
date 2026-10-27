"""Fixtures for the replay tests: a fresh copy of the app and a browser at 1280x720."""

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parent.parent / "app.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def app(tmp_path):
    """Start the app with the data of the recording and a throwaway password."""
    port, password = free_port(), secrets.token_urlsafe(9)
    env = os.environ | {"APP_ADMIN_PASSWORD": password, "PYTHONUTF8": "1"}
    process = subprocess.Popen(
        [sys.executable, str(APP), "--reset", "--port", str(port), "--db", str(tmp_path / "app.db")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/login", timeout=1)
            break
        except OSError:
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read().decode(errors="replace"))
            time.sleep(0.1)
    try:
        yield {"url": url, "password": password}
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 720}, locale="es-PE")
    yield context.new_page()
    context.close()
