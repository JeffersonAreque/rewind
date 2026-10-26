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
from datetime import date, datetime
from functools import wraps

from flask import Flask, flash, get_flashed_messages, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import db

app = Flask(__name__)
app.secret_key = os.getenv("LEGACY_SECRET_KEY") or secrets.token_hex(32)

# Old systems were slow; a short pause makes the hourglass visible on video.
POST_DELAY_SECONDS = 0.35

ERRORS = {
    "bad_login": (0x0013, "USUARIO O CLAVE INCORRECTOS"),
    "required": (0x0042, "CAMPO OBLIGATORIO"),
    "bad_document": (0x0071, "RUC/DNI INVALIDO"),
    "bad_email": (0x0075, "CORREO INVALIDO"),
    "bad_date": (0x0057, "FECHA INVALIDA"),
    "duplicate_client": (0x0088, "CLIENTE YA REGISTRADO"),
    "no_records": (0x00B2, "NO SE ENCONTRARON REGISTROS"),
}

ESTADOS = ("", "PENDIENTE", "PARCIAL", "PAGADA")

CLIENTE_FIELDS = ("documento", "razon_social", "direccion", "telefono", "correo")
CLIENTE_REQUIRED = {"documento": "RUC / DNI", "razon_social": "RAZON SOCIAL", "direccion": "DIRECCION"}
RUC_PREFIXES = ("10", "15", "17", "20")

# The old codepage had no accented vowels.
NO_ACCENTS = str.maketrans("ÁÉÍÓÚÜ", "AEIOUU")


def legacy_text(value: str | None) -> str:
    """Uppercase, accent-free, single-spaced text, as the old system stored it."""
    return " ".join((value or "").split()).upper().translate(NO_ACCENTS)


def parse_date(value: str) -> str | None:
    """DD/MM/AAAA -> ISO date, or None if it is not a real date."""
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


@app.template_filter("dmy")
def dmy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


@app.template_filter("soles")
def soles(cents: int) -> str:
    return f"S/ {cents / 100:,.2f}"


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


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


@app.route("/login", methods=["GET", "POST"])
def login():
    form = {"usuario": legacy_text(request.form.get("usuario"))}
    if request.method == "GET":
        return screen("login.html", "INICIO DE SESION", form=form)

    clave = request.form.get("clave", "")
    if not form["usuario"]:
        return screen("login.html", "INICIO DE SESION", error("required", "usuario", "USUARIO"), form=form)
    if not clave:
        return screen("login.html", "INICIO DE SESION", error("required", "clave", "CLAVE"), form=form)

    with db.transaction() as conn:
        user =conn.execute("SELECT * FROM usuarios WHERE usuario = ?", (form["usuario"],)).fetchone()
    if user is None or not check_password_hash(user["clave_hash"], clave):
        return screen("login.html", "INICIO DE SESION", error("bad_login", "clave"), form=form)

    session["usuario"] = user["usuario"]
    return redirect(url_for("clientes"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/acerca")
@login_required
def acerca():
    flash("SISTEMA DE FACTURACION v2.3 - (C) 1999 SISTEMAS AMAZONIA S.R.L.")
    return redirect(request.referrer or url_for("clientes"))


def validate_cliente(form: dict, conn) -> dict | None:
    for field, label in CLIENTE_REQUIRED.items():
        if not form[field]:
            return error("required", field, label)
    documento = form["documento"]
    is_dni = len(documento) == 8
    is_ruc = len(documento) == 11 and documento.startswith(RUC_PREFIXES)
    if not documento.isdigit() or not (is_dni or is_ruc):
        return error("bad_document", "documento", "DNI: 8 DIGITOS / RUC: 11 DIGITOS")
    if form["correo"] and "@" not in form["correo"]:
        return error("bad_email", "correo")
    if conn.execute("SELECT 1 FROM clientes WHERE documento = ?", (documento,)).fetchone():
        return error("duplicate_client", "documento", documento)
    return None


@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    form = {field: legacy_text(request.form.get(field)) for field in CLIENTE_FIELDS}
    dialog = None
    with db.transaction() as conn:
        if request.method == "POST":
            dialog = validate_cliente(form, conn)
            if dialog is None:
                conn.execute(
                    "INSERT INTO clientes (documento, razon_social, direccion, telefono, correo) VALUES (?, ?, ?, ?, ?)",
                    tuple(form[field] for field in CLIENTE_FIELDS),
                )
                flash("OPERACION COMPLETADA SATISFACTORIAMENTE.")
                return redirect(url_for("clientes"))
        lista = conn.execute("SELECT * FROM clientes ORDER BY id DESC").fetchall()
    return screen(
        "clientes.html", "REGISTRO DE CLIENTES", dialog, status=f"{len(lista)} REGISTROS", form=form, lista=lista
    )


def search_facturas(form: dict, conn) -> tuple[list, dict | None]:
    where, params = [], []
    if form["numero"]:
        where.append("numero LIKE ?")
        params.append(f"%{form['numero']}%")
    if form["cliente"]:
        where.append("cliente_id = ?")
        params.append(form["cliente"])
    if form["estado"] in ESTADOS[1:]:
        where.append("estado = ?")
        params.append(form["estado"])
    for field, op in (("desde", ">="), ("hasta", "<=")):
        if form[field]:
            iso = parse_date(form[field])
            if iso is None:
                return [], error("bad_date", field, "FORMATO: DD/MM/AAAA")
            where.append(f"fecha {op} ?")
            params.append(iso)
    sql = "SELECT * FROM facturas_saldo"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql + " ORDER BY numero", params).fetchall()
    return rows, None if rows else error("no_records", "numero")


@app.route("/facturas")
@login_required
def facturas():
    form = {field: legacy_text(request.args.get(field)) for field in ("numero", "cliente", "estado", "desde", "hasta")}
    resultados, dialog = [], None
    with db.transaction() as conn:
        clientes = conn.execute("SELECT id, razon_social FROM clientes ORDER BY razon_social").fetchall()
        if request.args.get("buscar"):
            resultados, dialog = search_facturas(form, conn)
    saldo = sum(f["saldo"] for f in resultados)
    status = f"{len(resultados)} REGISTROS - SALDO TOTAL {soles(saldo)}" if resultados else "LISTO"
    return screen(
        "facturas.html",
        "BUSQUEDA DE FACTURAS",
        dialog,
        status=status,
        form=form,
        clientes=clientes,
        estados=ESTADOS,
        resultados=resultados,
        selected=request.args.get("sel", ""),
    )


@app.route("/pagos")
@login_required
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
