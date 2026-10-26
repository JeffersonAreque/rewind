"""Record the legacy demo video automatically, following docs/grabar-video.md.

Starts legacy-demo with fresh data, drives it with Playwright at human speed
(slow mouse, typed keys, ~1 s pause per screen) and saves a compressed MP4.

Run:  uv run python scripts/record_demo.py [--output samples/facturacion-demo.mp4] [--headed]
Needs: ffmpeg on PATH and `uv run playwright install chromium` once.
"""

import argparse
import math
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SIZE = {"width": 1280, "height": 720}
PAUSE = 1.0  # seconds to rest on each screen and message box
TYPE_DELAY_MS = 90

# Recorded video has no system cursor, so the page draws a Windows 98 one.
# It survives page loads by remembering its position in sessionStorage.
CURSOR_SCRIPT = """
(() => {
  const ARROW = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="20" shape-rendering="crispEdges">' +
    '<path d="M.5 .5v16l4-4 3 6.5 2-1-3-6.5h5.5z" fill="#fff" stroke="#000"/></svg>');
  const HOURGLASS = "/static/icons/hourglass.svg";

  function init() {
    const cursor = document.createElement("img");
    cursor.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;left:0;top:0;";
    document.body.appendChild(cursor);
    let x = Number(sessionStorage.getItem("cx") || 640);
    let y = Number(sessionStorage.getItem("cy") || 360);

    function draw() {
      const busy = document.body.classList.contains("busy");
      const src = busy ? HOURGLASS : ARROW;
      if (cursor.getAttribute("src") !== src) cursor.src = src;
      const offset = busy ? 16 : 0;
      cursor.style.transform = `translate(${x - offset}px, ${y - offset}px)`;
      requestAnimationFrame(draw);
    }

    document.addEventListener("mousemove", (event) => {
      x = event.clientX;
      y = event.clientY;
      sessionStorage.setItem("cx", x);
      sessionStorage.setItem("cy", y);
    }, true);
    draw();
  }

  if (document.body) init();
  else document.addEventListener("DOMContentLoaded", init);
})();
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_app(port: int) -> subprocess.Popen:
    app = subprocess.Popen(
        [sys.executable, str(ROOT / "legacy-demo" / "app.py"), "--reset", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=1)
            return app
        except OSError:
            time.sleep(0.2)
    app.terminate()
    raise RuntimeError("legacy-demo did not start")


class Actor:
    """Moves the mouse and types like a person, so the video is easy to follow."""

    def __init__(self, page: Page):
        self.page = page
        self.x, self.y = SIZE["width"] / 2, SIZE["height"] / 2

    def move_to(self, target: Locator) -> None:
        box = target.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        duration = min(1.0, max(0.3, math.dist((self.x, self.y), (x, y)) / 900))
        steps = int(duration / 0.016)
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            self.page.mouse.move(self.x + (x - self.x) * ease, self.y + (y - self.y) * ease)
            time.sleep(0.016)
        self.x, self.y = x, y

    def click(self, target: Locator) -> None:
        self.move_to(target)
        time.sleep(0.2)
        self.page.mouse.down()
        time.sleep(0.08)
        self.page.mouse.up()

    def click_and_wait(self, target: Locator) -> None:
        """Click something that loads a new page."""
        with self.page.expect_navigation():
            self.click(target)
        time.sleep(PAUSE)

    def type_into(self, target: Locator, text: str) -> None:
        self.click(target)
        target.press_sequentially(text, delay=TYPE_DELAY_MS)
        time.sleep(0.3)

    def choose(self, target: Locator, label: str) -> None:
        # Headless Chromium does not paint native dropdowns; the value change is what shows.
        self.move_to(target)
        time.sleep(0.3)
        target.select_option(label=label)
        time.sleep(0.5)

    def accept_dialog(self) -> None:
        time.sleep(PAUSE)
        self.click(self.page.locator("#dialog .actions button"))
        time.sleep(0.5)


def follow_script(page: Page, me: Actor) -> None:
    """The steps of docs/grabar-video.md."""
    # 1. Login
    time.sleep(PAUSE)
    me.type_into(page.locator("#usuario"), "admin")
    me.type_into(page.locator("#clave"), "admin")
    me.click_and_wait(page.get_by_role("button", name="ACEPTAR"))

    # 2. Customers: first the missing-field error, then a valid customer
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()
    me.type_into(page.locator("#documento"), "20612345678")
    me.type_into(page.locator("#razon_social"), "FERRETERIA EL CASTAÑAL S.A.C.")
    me.type_into(page.locator("#direccion"), "JR. LORETO 455 - PUERTO MALDONADO")
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()

    # 3. Invoice search
    me.click_and_wait(page.locator(".menubar").get_by_role("link", name="FACTURAS"))
    me.choose(page.locator("#cliente"), "INVERSIONES TAMBOPATA S.A.C.")
    me.choose(page.locator("#estado"), "PENDIENTE")
    me.click_and_wait(page.get_by_role("button", name="BUSCAR"))
    me.click(page.locator("tr", has_text="F001-000110"))
    time.sleep(PAUSE)
    me.click_and_wait(page.get_by_role("button", name="REGISTRAR PAGO"))

    # 4. Payment
    me.type_into(page.locator("#monto"), "300.00")
    me.click(page.locator("label", has_text="TRANSFERENCIA"))
    time.sleep(0.3)
    me.type_into(page.locator("#operacion"), "OP-784512")
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))

    # 5. Back on the search: invoice now PARCIAL
    me.accept_dialog()
    time.sleep(PAUSE * 2)


def compress(raw: Path, output: Path, trim_start: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{trim_start:.2f}", "-i", str(raw),
            "-vf", "fps=30,format=yuv420p",
            "-c:v", "libx264", "-crf", "28", "-preset", "slow",
            "-movflags", "+faststart", "-an",
            str(output),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "samples" / "facturacion-demo.mp4")
    parser.add_argument("--headed", action="store_true", help="show the browser while recording")
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")

    port = free_port()
    app = start_app(port)
    try:
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport=SIZE, record_video_dir=tmp, record_video_size=SIZE)
            context.add_init_script(CURSOR_SCRIPT)
            started = time.monotonic()
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/login")
            trim_start = time.monotonic() - started  # drop the blank frames before the first screen

            follow_script(page, Actor(page))

            raw = Path(page.video.path())
            context.close()  # finishes writing the video
            browser.close()
            compress(raw, args.output, trim_start)
    finally:
        app.terminate()

    size_mb = args.output.stat().st_size / 1_000_000
    print(f"saved {args.output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
