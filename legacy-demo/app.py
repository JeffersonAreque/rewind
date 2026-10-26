"""SISTEMA DE FACTURACION v2.3 - a fake legacy billing app (Windows 98 look).

It exists to be recorded on video and modernized by Rewind. Every action is a
full page reload and every validation happens on the server, like the
client/server programs of the late 90s.

Run:  uv run python legacy-demo/app.py [--reset] [--port 5000]
"""

import argparse
import os
import secrets
import time
from datetime import date

from flask import Flask, flash, get_flashed_messages, redirect, render_template, request, session, url_for

import db

app = Flask(__name__)
app.secret_key = os.getenv("LEGACY_SECRET_KEY") or secrets.token_hex(32)

# Old systems were slow; a short pause makes the hourglass visible on video.
POST_DELAY_SECONDS = 0.35

ERRORS = {
    "required": (0x0042, "CAMPO OBLIGATORIO"),
}


def error(kind: str, field: str | None = None, detail: str | None = None) -> dict:
    code, text = ERRORS[kind]
    return {"kind": "error", "title": "ERROR", "message": f"ERROR 0x{code:04X}: {text}", "detail": detail, "focus": field}


def info(message: str) -> dict:
    return {"kind": "info", "title": "SISTEMA DE FACTURACION", "message": message, "detail": None, "focus": None}


def screen(template: str, title: str, dialog: dict | None = None, status: str = "LISTO", **context):
    if dialog is None:
        flashed = get_flashed_messages()
        if flashed:
            dialog = info(flashed[0])
    return render_template(
        template,
        title=title,
        dialog=dialog,
        status=status,
        usuario=session.get("usuario", ""),
        today=date.today().strftime("%d/%m/%Y"),
        **context,
    )


@app.before_request
def slow_down_posts():
    if request.method == "POST":
        time.sleep(POST_DELAY_SECONDS)


@app.route("/")
def index():
    return redirect(url_for("clientes"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/acerca")
def acerca():
    flash("SISTEMA DE FACTURACION v2.3 - (C) 1999 SISTEMAS AMAZONIA S.R.L.")
    return redirect(url_for("clientes"))


@app.route("/clientes")
def clientes():
    return screen("app.html", "REGISTRO DE CLIENTES")


@app.route("/facturas")
def facturas():
    return screen("app.html", "BUSQUEDA DE FACTURAS")


@app.route("/pagos")
def pagos():
    return screen("app.html", "REGISTRAR PAGO")


def main() -> None:
    parser = argparse.ArgumentParser(description="Legacy billing demo app")
    parser.add_argument("--reset", action="store_true", help="delete the database and reseed it")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    db.init_db(reset=args.reset)
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
