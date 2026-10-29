"""SISTEMA DE CITAS v1.4 - a fake legacy clinic appointments app (full-screen Windows 9x look).

The second sample for Rewind, built to differ from legacy-demo/: another
domain, a full-screen window with an F-key toolbar instead of menus, error
codes like E-101, a home screen that is not the first menu entry, and a rule
the billing demo does not have (a slot that is already taken cannot be booked).
Every action is a full page reload and every validation runs on the server.

Run:  uv run python legacy-clinica/app.py [--reset] [--port 5002]
"""

import argparse
import os
import secrets
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from flask import (Flask, flash, get_flashed_messages, redirect, render_template, request, send_from_directory,
                   session, url_for)
from werkzeug.security import check_password_hash

import db

# 98.css, its fonts and the message box icons are shared with legacy-demo/.
SHARED = Path(__file__).resolve().parent.parent / "legacy-demo" / "static"

app = Flask(__name__)
app.secret_key = os.getenv("LEGACY_SECRET_KEY") or secrets.token_hex(32)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600  # keep fonts cached, so pages never show without text

POST_DELAY_SECONDS = 0.25

ERRORS = {
    "bad_login": ("E-010", "OPERADOR O CONTRASENA INVALIDOS"),
    "required": ("E-101", "DATO REQUERIDO"),
    "bad_dni": ("E-102", "DNI DEBE TENER 8 DIGITOS"),
    "duplicate": ("E-103", "PACIENTE YA EXISTE"),
    "bad_date": ("E-120", "FECHA INVALIDA"),
    "taken": ("E-305", "TURNO NO DISPONIBLE"),
    "no_slots": ("E-310", "NO HAY TURNOS PARA LOS CRITERIOS"),
}

SEGUROS = ("SIS", "ESSALUD", "PARTICULAR")
TIPOS = ("PRIMERA VEZ", "CONTROL")
ESPECIALIDADES = ("MEDICINA GENERAL", "PEDIATRIA", "GINECOLOGIA")
ESTADOS = ("", "LIBRE", "RESERVADA")
PACIENTE_FIELDS = ("dni", "apellidos", "nombres", "fecha_nac", "telefono", "seguro")
PACIENTE_REQUIRED = {"dni": "DNI", "apellidos": "APELLIDOS", "nombres": "NOMBRES"}

NO_ACCENTS = str.maketrans("ÁÉÍÓÚÜ", "AEIOUU")


def legacy_text(value: str | None) -> str:
    return " ".join((value or "").split()).upper().translate(NO_ACCENTS)


def parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


@app.template_filter("dmy")
def dmy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else ""


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "operador" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


def error(kind: str, field: str | None = None, detail: str | None = None) -> dict:
    code, text = ERRORS[kind]
    message = f"{code} {text}" + (f": {detail}" if detail else "")
    return {"kind": "error", "message": message, "focus": field}


def screen(template: str, title: str, dialog: dict | None = None, status: str = "", **context):
    if dialog is None and (flashed := get_flashed_messages()):
        dialog = {"kind": "info", "message": flashed[0], "focus": None}
    return render_template(template, title=title, dialog=dialog, status=status,
                           operador=session.get("operador", ""), today=date.today().strftime("%d/%m/%Y"), **context)


@app.before_request
def slow_down_posts():
    if request.method == "POST":
        time.sleep(POST_DELAY_SECONDS)


@app.route("/shared/<path:name>")
def shared(name):
    return send_from_directory(SHARED, name)


@app.route("/")
def index():
    return redirect(url_for("agenda"))


@app.route("/login", methods=["GET", "POST"])
def login():
    form = {"operador": legacy_text(request.form.get("operador"))}
    if request.method == "GET":
        return screen("login.html", "ACCESO AL SISTEMA", form=form)
    clave = request.form.get("clave", "")
    if not form["operador"]:
        return screen("login.html", "ACCESO AL SISTEMA", error("required", "operador", "OPERADOR"), form=form)
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM operadores WHERE operador = ?", (form["operador"],)).fetchone()
    if user is None or not check_password_hash(user["clave_hash"], clave):
        return screen("login.html", "ACCESO AL SISTEMA", error("bad_login", "clave"), form=form)
    session["operador"] = user["operador"]
    return redirect(url_for("agenda"))


@app.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("login"))


def validate_paciente(form: dict, conn) -> dict | None:
    for field, label in PACIENTE_REQUIRED.items():
        if not form[field]:
            return error("required", field, label)
    if not (form["dni"].isdigit() and len(form["dni"]) == 8):
        return error("bad_dni", "dni")
    if form["fecha_nac"] and parse_date(form["fecha_nac"]) is None:
        return error("bad_date", "fecha_nac", "FORMATO DD/MM/AAAA")
    if conn.execute("SELECT 1 FROM pacientes WHERE dni = ?", (form["dni"],)).fetchone():
        return error("duplicate", "dni", form["dni"])
    return None


@app.route("/pacientes", methods=["GET", "POST"])
@login_required
def pacientes():
    form = {field: legacy_text(request.form.get(field)) for field in PACIENTE_FIELDS}
    form["seguro"] = form["seguro"] if form["seguro"] in SEGUROS else "PARTICULAR"
    dialog = None
    with db.transaction() as conn:
        if request.method == "POST":
            dialog = validate_paciente(form, conn)
            if dialog is None:
                values = form | {"fecha_nac": parse_date(form["fecha_nac"]) or ""}
                conn.execute("INSERT INTO pacientes (dni, apellidos, nombres, fecha_nac, telefono, seguro) "
                             "VALUES (:dni, :apellidos, :nombres, :fecha_nac, :telefono, :seguro)", values)
                flash("DATOS GRABADOS CORRECTAMENTE")
                return redirect(url_for("pacientes"))
        lista = conn.execute("SELECT * FROM pacientes ORDER BY id").fetchall()
    return screen("pacientes.html", "MANTENIMIENTO DE PACIENTES", dialog, status=f"{len(lista)} PACIENTES",
                  form=form, lista=lista, seguros=SEGUROS)


SLOTS = """SELECT t.id, t.fecha, t.hora, m.nombre AS medico, m.especialidad, m.consultorio,
                  COALESCE(p.apellidos || ' ' || p.nombres, '') AS paciente,
                  CASE WHEN t.paciente_id IS NULL THEN 'LIBRE' ELSE 'RESERVADA' END AS estado
           FROM turnos t JOIN medicos m ON m.id = t.medico_id LEFT JOIN pacientes p ON p.id = t.paciente_id"""


@app.route("/agenda")
@login_required
def agenda():
    form = {f: legacy_text(request.args.get(f)) for f in ("especialidad", "medico", "fecha", "estado")}
    turnos, dialog = [], None
    with db.transaction() as conn:
        medicos = conn.execute("SELECT id, nombre FROM medicos ORDER BY nombre").fetchall()
        if request.args.get("consultar"):
            where, params = [], []
            if form["especialidad"]:
                where.append("m.especialidad = ?")
                params.append(form["especialidad"])
            if form["medico"]:
                where.append("m.id = ?")
                params.append(form["medico"])
            if form["fecha"]:
                iso = parse_date(form["fecha"])
                if iso is None:
                    dialog = error("bad_date", "fecha", "FORMATO DD/MM/AAAA")
                where.append("t.fecha = ?")
                params.append(iso)
            if form["estado"] == "LIBRE":
                where.append("t.paciente_id IS NULL")
            elif form["estado"] == "RESERVADA":
                where.append("t.paciente_id IS NOT NULL")
            if dialog is None:
                sql = SLOTS + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY t.hora, m.nombre"
                turnos = conn.execute(sql, params).fetchall()
                dialog = None if turnos else error("no_slots")
    libres = sum(t["estado"] == "LIBRE" for t in turnos)
    return screen("agenda.html", "AGENDA DE CITAS", dialog, status=f"TURNOS LIBRES: {libres}" if turnos else "",
                  form=form, medicos=medicos, especialidades=ESPECIALIDADES, estados=ESTADOS, turnos=turnos,
                  selected=request.args.get("sel", ""),
                  back=urlencode({k: v for k, v in request.args.items() if k != "sel"}))


@app.route("/reserva", methods=["GET", "POST"])
@login_required
def reserva():
    source = request.form if request.method == "POST" else request.args
    form = {f: legacy_text(source.get(f)) for f in ("paciente", "tipo", "motivo")}
    form["tipo"] = form["tipo"] if form["tipo"] in TIPOS else TIPOS[0]
    back = source.get("back", "")  # the agenda search to return to
    dialog = None
    with db.transaction() as conn:
        turno = conn.execute(SLOTS + " WHERE t.id = ?", (source.get("turno", ""),)).fetchone()
        if turno is None:
            return redirect(url_for("agenda"))
        pacientes = conn.execute("SELECT id, apellidos || ' ' || nombres AS nombre FROM pacientes ORDER BY apellidos").fetchall()
        if request.method == "POST":
            if not form["paciente"]:
                dialog = error("required", "paciente", "PACIENTE")
            elif turno["estado"] != "LIBRE":
                dialog = error("taken", None, f"{turno['hora']} {turno['medico']}")
            else:
                conn.execute("INSERT INTO citas (turno_id, paciente_id, tipo, motivo) VALUES (?, ?, ?, ?)",
                             (turno["id"], form["paciente"], form["tipo"], form["motivo"]))
                conn.execute("UPDATE turnos SET paciente_id = ? WHERE id = ?", (form["paciente"], turno["id"]))
                flash("CITA RESERVADA")
                return redirect(url_for("agenda") + "?" + back + f"&sel={turno['id']}")
    return screen("reserva.html", "RESERVA DE CITA", dialog, form=form, turno=turno, pacientes=pacientes,
                  tipos=TIPOS, back=back)


def main() -> None:
    parser = argparse.ArgumentParser(description="Legacy clinic appointments demo app")
    parser.add_argument("--reset", action="store_true", help="delete the database and reseed it")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    db.init_db(reset=args.reset)
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
