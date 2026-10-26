"""SQLite schema and fictitious seed data for the legacy billing system.

Amounts are stored in cents (integers). An invoice's balance and status are
derived from its payments, so they can never drift apart.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).resolve().parent / "data" / "facturacion.db"

SCHEMA = """
CREATE TABLE usuarios (
    id INTEGER PRIMARY KEY,
    usuario TEXT NOT NULL UNIQUE,
    clave_hash TEXT NOT NULL
);
CREATE TABLE clientes (
    id INTEGER PRIMARY KEY,
    documento TEXT NOT NULL UNIQUE,
    razon_social TEXT NOT NULL,
    direccion TEXT NOT NULL,
    telefono TEXT NOT NULL DEFAULT '',
    correo TEXT NOT NULL DEFAULT ''
);
CREATE TABLE facturas (
    id INTEGER PRIMARY KEY,
    numero TEXT NOT NULL UNIQUE,
    fecha TEXT NOT NULL,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
    total INTEGER NOT NULL
);
CREATE TABLE pagos (
    id INTEGER PRIMARY KEY,
    factura_id INTEGER NOT NULL REFERENCES facturas(id),
    fecha TEXT NOT NULL,
    monto INTEGER NOT NULL,
    metodo TEXT NOT NULL,
    operacion TEXT NOT NULL DEFAULT ''
);
CREATE VIEW facturas_saldo AS
SELECT f.id, f.numero, f.fecha, f.cliente_id, c.razon_social, f.total,
       f.total - COALESCE(SUM(p.monto), 0) AS saldo,
       CASE
           WHEN COALESCE(SUM(p.monto), 0) = 0 THEN 'PENDIENTE'
           WHEN COALESCE(SUM(p.monto), 0) >= f.total THEN 'PAGADA'
           ELSE 'PARCIAL'
       END AS estado
FROM facturas f
JOIN clientes c ON c.id = f.cliente_id
LEFT JOIN pagos p ON p.factura_id = f.id
GROUP BY f.id;
"""

# Fictitious companies; any resemblance to real ones is coincidental.
CLIENTES = [
    ("20600111222", "INVERSIONES TAMBOPATA S.A.C.", "AV. LEON VELARDE 845 - PUERTO MALDONADO", "082-571234", "compras@tambopata.example.com"),
    ("20600333444", "DISTRIBUIDORA LOS AMIGOS E.I.R.L.", "JR. PUNO 312 - PUERTO MALDONADO", "082-572201", "ventas@losamigos.example.com"),
    ("20600555666", "AGROINDUSTRIAS MADRE DE DIOS S.R.L.", "CARRETERA INTEROCEANICA KM 4.5", "082-573380", "admin@agromdd.example.com"),
    ("20600777888", "TRANSPORTES RIO INAMBARI S.A.", "AV. 28 DE JULIO 1020 - LABERINTO", "082-574415", "operaciones@inambari.example.com"),
    ("10456789012", "QUISPE MAMANI ROSA ELENA", "JR. AREQUIPA 155 - PUERTO MALDONADO", "982-114455", ""),
    ("20600999000", "CONSTRUCTORA HUEPETUHE S.A.C.", "AV. FITZCARRALD 640 - PUERTO MALDONADO", "082-575590", "logistica@huepetuhe.example.com"),
]

# (numero, fecha, cliente index, total in cents)
FACTURAS = [
    ("F001-000101", "2026-09-02", 0, 125000),
    ("F001-000102", "2026-09-05", 1, 48050),
    ("F001-000103", "2026-09-09", 2, 310075),
    ("F001-000104", "2026-09-12", 0, 87500),
    ("F001-000105", "2026-09-18", 3, 156000),
    ("F001-000106", "2026-09-23", 4, 23590),
    ("F001-000107", "2026-09-29", 5, 542000),
    ("F001-000108", "2026-10-01", 1, 99900),
    ("F001-000109", "2026-10-06", 2, 178045),
    ("F001-000110", "2026-10-09", 0, 64000),
    ("F001-000111", "2026-10-13", 3, 212500),
    ("F001-000112", "2026-10-16", 5, 133370),
    ("F001-000113", "2026-10-20", 4, 15000),
    ("F001-000114", "2026-10-22", 1, 275000),
    ("F001-000115", "2026-10-24", 0, 190080),
]

# (factura index, fecha, monto in cents, metodo, operacion)
PAGOS = [
    (0, "2026-09-10", 125000, "TRANSFERENCIA", "OP-778812"),
    (1, "2026-09-15", 48050, "EFECTIVO", ""),
    (2, "2026-09-30", 150000, "CHEQUE", "CH-004521"),
    (4, "2026-10-01", 156000, "TRANSFERENCIA", "OP-781290"),
    (6, "2026-10-08", 200000, "TRANSFERENCIA", "OP-783305"),
    (7, "2026-10-10", 99900, "EFECTIVO", ""),
    (10, "2026-10-20", 100000, "CHEQUE", "CH-004688"),
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
    with transaction() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO usuarios (usuario, clave_hash) VALUES (?, ?)",
            ("ADMIN", generate_password_hash("admin")),
        )
        conn.executemany(
            "INSERT INTO clientes (documento, razon_social, direccion, telefono, correo) VALUES (?, ?, ?, ?, ?)",
            [(d, r, a, t, c.upper()) for d, r, a, t, c in CLIENTES],
        )
        cliente_ids = [row["id"] for row in conn.execute("SELECT id FROM clientes ORDER BY id")]
        conn.executemany(
            "INSERT INTO facturas (numero, fecha, cliente_id, total) VALUES (?, ?, ?, ?)",
            [(n, f, cliente_ids[c], t) for n, f, c, t in FACTURAS],
        )
        factura_ids = [row["id"] for row in conn.execute("SELECT id FROM facturas ORDER BY id")]
        conn.executemany(
            "INSERT INTO pagos (factura_id, fecha, monto, metodo, operacion) VALUES (?, ?, ?, ?, ?)",
            [(factura_ids[f], d, m, met, op) for f, d, m, met, op in PAGOS],
        )
