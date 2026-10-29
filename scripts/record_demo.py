"""Record a legacy demo video automatically.

Starts a legacy app with fresh data, drives it with Playwright and saves a
compressed MP4. Two apps:
- facturacion (legacy-demo/): the steps of docs/grabar-video.md at an easy
  pace (slow mouse, typed keys, ~1 s pause per screen);
- clinica (legacy-clinica/): a receptionist working the way people really do,
  with fast clicks and typing, barely any pauses, a typo fixed with backspace
  and two mistakes the program rejects. Use --fast for that pace.

Run:  uv run python scripts/record_demo.py [--app clinica --fast] [--output FILE] [--headed]
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
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SIZE = {"width": 1280, "height": 720}


@dataclass(frozen=True)
class Pace:
    pause: float  # seconds to rest on each screen and message box
    type_delay_ms: int
    min_move: float  # seconds for the shortest and longest mouse moves
    max_move: float
    px_per_second: float
    before_click: float


EASY = Pace(pause=1.0, type_delay_ms=90, min_move=0.3, max_move=1.0, px_per_second=900, before_click=0.2)
FAST = Pace(pause=0.3, type_delay_ms=40, min_move=0.1, max_move=0.35, px_per_second=2600, before_click=0.05)

# Recorded video has no system cursor, so the page draws a Windows 98 one.
# It survives page loads by remembering its position in sessionStorage.
CURSOR_SCRIPT = """
(() => {
  const ARROW = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="20" shape-rendering="crispEdges">' +
    '<path d="M.5 .5v16l4-4 3 6.5 2-1-3-6.5h5.5z" fill="#fff" stroke="#000"/></svg>');
  const HOURGLASS = "__HOURGLASS__";

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


def start_app(script: Path, port: int) -> subprocess.Popen:
    app = subprocess.Popen(
        [sys.executable, str(script), "--reset", "--port", str(port)],
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
    raise RuntimeError(f"{script.parent.name} did not start")


class Actor:
    """Moves the mouse and types like a person, at an easy or a fast pace."""

    def __init__(self, page: Page, pace: Pace):
        self.page = page
        self.pace = pace
        self.x, self.y = SIZE["width"] / 2, SIZE["height"] / 2

    def move_to(self, target: Locator) -> None:
        box = target.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pace = self.pace
        duration = min(pace.max_move, max(pace.min_move, math.dist((self.x, self.y), (x, y)) / pace.px_per_second))
        steps = max(1, int(duration / 0.016))
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            self.page.mouse.move(self.x + (x - self.x) * ease, self.y + (y - self.y) * ease)
            time.sleep(0.016)
        self.x, self.y = x, y

    def click(self, target: Locator) -> None:
        self.move_to(target)
        time.sleep(self.pace.before_click)
        self.page.mouse.down()
        time.sleep(0.06)
        self.page.mouse.up()

    def click_and_wait(self, target: Locator) -> None:
        """Click something that loads a new page."""
        with self.page.expect_navigation():
            self.click(target)
        time.sleep(self.pace.pause)

    def type_into(self, target: Locator, text: str) -> None:
        self.click(target)
        target.press_sequentially(text, delay=self.pace.type_delay_ms)
        time.sleep(self.pace.pause * 0.3)

    def fix_typo(self, target: Locator, typed: str, wrong: int, rest: str) -> None:
        """Type `typed`, notice the last `wrong` characters are a mistake, erase them and finish."""
        self.type_into(target, typed)
        for _ in range(wrong):
            target.press("Backspace", delay=self.pace.type_delay_ms)
        target.press_sequentially(rest, delay=self.pace.type_delay_ms)
        time.sleep(self.pace.pause * 0.3)

    def choose(self, target: Locator, label: str) -> None:
        # Headless Chromium does not paint native dropdowns; the value change is what shows.
        self.move_to(target)
        time.sleep(self.pace.before_click + 0.1)
        target.select_option(label=label)
        time.sleep(self.pace.pause * 0.5)

    def accept_dialog(self) -> None:
        time.sleep(self.pace.pause)
        self.click(self.page.locator("#dialog .actions button"))
        time.sleep(self.pace.pause * 0.5)


def follow_script(page: Page, me: Actor) -> None:
    """The steps of docs/grabar-video.md."""
    PAUSE = me.pace.pause
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


def follow_clinica(page: Page, me: Actor) -> None:
    """A receptionist registers a walk-in patient and books a pediatrics slot for today."""
    # 1. Login: the program opens on the agenda
    time.sleep(me.pace.pause)
    me.type_into(page.locator("#operador"), "recepcion")
    me.type_into(page.locator("#clave"), "clinica")
    me.click_and_wait(page.get_by_role("button", name="INGRESAR"))

    # 2. New patient: APELLIDOS forgotten, then typed with a typo fixed on the spot
    me.click_and_wait(page.locator(".toolbar").get_by_role("link", name="PACIENTES"))
    me.type_into(page.locator("#dni"), "45178236")
    me.type_into(page.locator("#nombres"), "juan carlos")
    me.click(page.locator("label", has_text="SIS").first)
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()
    me.fix_typo(page.locator("#apellidos"), "mamano", 1, "i quispe")
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()

    # 3. Agenda: pediatrics today; the first slot picked is already taken
    me.click_and_wait(page.locator(".toolbar").get_by_role("link", name="AGENDA"))
    me.choose(page.locator("#especialidad"), "PEDIATRIA")
    me.type_into(page.locator("#fecha"), date.today().strftime("%d/%m/%Y"))
    me.click_and_wait(page.get_by_role("button", name="CONSULTAR"))
    me.click(page.locator("tr", has_text="09:00").filter(has_text="VARGAS"))
    me.click_and_wait(page.get_by_role("button", name="RESERVAR"))
    me.choose(page.locator("#paciente"), "MAMANI QUISPE JUAN CARLOS")
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()
    me.click_and_wait(page.get_by_role("button", name="CANCELAR"))

    # 4. A free slot: book it as a follow-up visit
    me.click(page.locator("tr", has_text="09:30").filter(has_text="VARGAS"))
    me.click_and_wait(page.get_by_role("button", name="RESERVAR"))
    me.choose(page.locator("#paciente"), "MAMANI QUISPE JUAN CARLOS")
    me.click(page.locator("label", has_text="CONTROL"))
    me.type_into(page.locator("#motivo"), "fiebre y tos")
    me.click_and_wait(page.get_by_role("button", name="GRABAR"))
    me.accept_dialog()
    time.sleep(me.pace.pause * 3)


# app: (server script, scenario, default video, hourglass cursor URL)
APPS = {
    "facturacion": (ROOT / "legacy-demo" / "app.py", follow_script, ROOT / "samples" / "facturacion-demo.mp4",
                    "/static/icons/hourglass.svg"),
    "clinica": (ROOT / "legacy-clinica" / "app.py", follow_clinica, ROOT / "samples" / "clinica-demo.mp4",
                "/shared/icons/hourglass.svg"),
}


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
    parser.add_argument("--app", choices=APPS, default="facturacion", help="legacy app to record")
    parser.add_argument("--fast", action="store_true", help="fast clicks and typing, barely any pauses")
    parser.add_argument("--output", type=Path, help="video file (default samples/<app>-demo.mp4)")
    parser.add_argument("--headed", action="store_true", help="show the browser while recording")
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")
    server, scenario, default_output, hourglass = APPS[args.app]
    args.output = args.output or default_output

    port = free_port()
    app = start_app(server, port)
    try:
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport=SIZE, record_video_dir=tmp, record_video_size=SIZE)
            context.add_init_script(CURSOR_SCRIPT.replace("__HOURGLASS__", hourglass))
            started = time.monotonic()
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/login")
            trim_start = time.monotonic() - started  # drop the blank frames before the first screen

            scenario(page, Actor(page, FAST if args.fast else EASY))

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
