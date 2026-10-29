"""SQLite schema and fictitious seed data for the legacy clinic appointments system.

Slots (turnos) are generated for the day the app starts, so the agenda of the
recording always shows "today", like the real program did.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).resolve().parent / "data" / "clinica.db"

SCHEMA = """
CREATE TABLE operadores (
    id INTEGER PRIMARY KEY,
    operador TEXT NOT NULL UNIQUE,
    clave_hash TEXT NOT NULL
);
CREATE TABLE pacientes (
    id INTEGER PRIMARY KEY,
    dni TEXT NOT NULL UNIQUE,
    apellidos TEXT NOT NULL,
    nombres TEXT NOT NULL,
    fecha_nac TEXT NOT NULL DEFAULT '',
    telefono TEXT NOT NULL DEFAULT '',
    seguro TEXT NOT NULL
);
CREATE TABLE medicos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    especialidad TEXT NOT NULL,
    consultorio TEXT NOT NULL
);
CREATE TABLE turnos (
    id INTEGER PRIMARY KEY,
    medico_id INTEGER NOT NULL REFERENCES medicos(id),
    fecha TEXT NOT NULL,
    hora TEXT NOT NULL,
    paciente_id INTEGER REFERENCES pacientes(id)
);
CREATE TABLE citas (
    id INTEGER PRIMARY KEY,
    turno_id INTEGER NOT NULL UNIQUE REFERENCES turnos(id),
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    tipo TEXT NOT NULL,
    motivo TEXT NOT NULL DEFAULT ''
);
"""

# Fictitious people; any resemblance to real ones is coincidental.
PACIENTES = [
    ("41235678", "TORRES VILCA", "ANA MARIA", "1985-03-14", "982-301144", "ESSALUD"),
    ("40987654", "CHAMBI HUAYTA", "PEDRO", "1979-11-02", "982-554120", "SIS"),
    ("72345610", "RIOS PAREDES", "LUCIANA", "2016-06-21", "993-110287", "SIS"),
    ("45670921", "SALAS NINA", "JORGE LUIS", "1990-01-30", "984-772310", "PARTICULAR"),
    ("71234098", "CCAHUANA ROJAS", "MATEO", "2019-09-08", "982-660034", "ESSALUD"),
    ("43218765", "PINEDO ARCE", "ROSA", "1968-05-17", "", "SIS"),
    ("70098123", "QUISPE TICONA", "VALENTINA", "2021-02-11", "995-402817", "PARTICULAR"),
    ("44102938", "MENDOZA CUTIPA", "RAUL", "1983-12-24", "982-118855", "ESSALUD"),
]

MEDICOS = [
    ("DR. CARLOS RAMIREZ TORRES", "MEDICINA GENERAL", "CONS. 101"),
    ("DRA. LUCIA VARGAS LEON", "PEDIATRIA", "CONS. 204"),
    ("DR. HUGO SALINAS MEZA", "PEDIATRIA", "CONS. 205"),
    ("DRA. MARIA HUAMAN CCORI", "GINECOLOGIA", "CONS. 310"),
]

# (medico index, hora, paciente index or None) for today's agenda
TURNOS = [
    (0, "08:00", 3), (0, "08:30", None), (0, "09:00", 5), (0, "09:30", None),
    (1, "08:00", 2), (1, "08:30", None), (1, "09:00", 4), (1, "09:30", None), (1, "10:00", None),
    (2, "08:00", None), (2, "08:30", 6),
    (3, "08:00", 0), (3, "08:30", None), (3, "09:00", None),
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(reset: bool = False) -> None:
    """Create and seed the database if it does not exist (or if reset)."""
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    if DB_PATH.exists():
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    with transaction() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO operadores (operador, clave_hash) VALUES (?, ?)",
                     ("RECEPCION", generate_password_hash("clinica")))
        conn.executemany(
            "INSERT INTO pacientes (dni, apellidos, nombres, fecha_nac, telefono, seguro) VALUES (?, ?, ?, ?, ?, ?)",
            PACIENTES,
        )
        conn.executemany("INSERT INTO medicos (nombre, especialidad, consultorio) VALUES (?, ?, ?)", MEDICOS)
        paciente_ids = [row["id"] for row in conn.execute("SELECT id FROM pacientes ORDER BY id")]
        medico_ids = [row["id"] for row in conn.execute("SELECT id FROM medicos ORDER BY id")]
        for medico, hora, paciente in TURNOS:
            paciente_id = None if paciente is None else paciente_ids[paciente]
            cur = conn.execute("INSERT INTO turnos (medico_id, fecha, hora, paciente_id) VALUES (?, ?, ?, ?)",
                               (medico_ids[medico], today, hora, paciente_id))
            if paciente_id:
                conn.execute("INSERT INTO citas (turno_id, paciente_id, tipo) VALUES (?, ?, 'CONTROL')",
                             (cur.lastrowid, paciente_id))
