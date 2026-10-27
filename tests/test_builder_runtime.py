"""The fixed runtime, driven by the hand-written demo spec (no model calls)."""

import copy
import importlib.util
import json
import re
import sys

import pytest

from rewind.config import ROOT

RUNTIME = ROOT / "rewind" / "builder" / "runtime"
FIXTURE = json.loads((ROOT / "samples" / "facturacion-demo.spec.json").read_text(encoding="utf-8"))
PASSWORD = "test-password"

sys.path.insert(0, str(RUNTIME))  # app.py imports expr from its own folder
_spec = importlib.util.spec_from_file_location("rewind_runtime_app", RUNTIME / "app.py")
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)


def make_client(tmp_path, monkeypatch, spec=None):
    monkeypatch.setenv("APP_ADMIN_PASSWORD", PASSWORD)
    spec = spec or FIXTURE["spec"]
    db = tmp_path / "app.db"
    runtime.init_db(runtime.Spec(spec), db, FIXTURE["seed"])
    app = runtime.create_app(spec, db)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.post("/login", data={"username": "admin", "password": PASSWORD})
    return c


def text(response) -> str:
    return re.sub(r"\s+", " ", response.get_data(as_text=True))


def test_login_required_and_wrong_password(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    assert c.get("/p/registro-de-clientes").headers["Location"].endswith("/login")
    assert "Usuario o clave incorrectos" in text(c.post("/login", data={"username": "ADMIN", "password": "nope"}))
    ok = c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    assert ok.headers["Location"].endswith("/p/registro-de-clientes")


def test_seed_is_loaded(client):
    page = text(client.get("/p/registro-de-clientes"))
    assert page.count('<tr data-id=') == 6
    assert "C0006" in page and "CONSTRUCTORA HUEPETUHE S.A.C." in page


def test_required_field_shows_legacy_code_inline(client):
    page = text(client.post("/p/registro-de-clientes", data={}))
    assert "ERROR 0x0042: CAMPO OBLIGATORIO" in page
    assert 'aria-invalid="true"' in page and "<dialog" not in page


def test_required_field_as_dialog_when_not_modernized(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["screens"][1]["error_style"] = "dialog"
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    page = text(c.post("/p/registro-de-clientes", data={}))
    assert "<dialog" in page and 'data-legacy="ACEPTAR"' in page and "0x0042" in page


def test_create_customer(client):
    saved = client.post("/p/registro-de-clientes", data={
        "f_document": "20612345678", "f_business_name": "FERRETERIA EL CASTAÑAL S.A.C.",
        "f_address": "JR. LORETO 455 - PUERTO MALDONADO",
    }, follow_redirects=True)
    page = text(saved)
    assert "OPERACION COMPLETADA SATISFACTORIAMENTE." in page and 'class="toast"' in page
    assert page.count('<tr data-id=') == 7 and "C0007" in page


def test_search_filters_and_selection_button(client):
    page = text(client.get("/p/busqueda-de-facturas?customer=1&status=PENDIENTE"))
    assert page.count('<tr data-id=') == 3
    assert re.search(r'data-legacy="REGISTRAR PAGO"[^>]*disabled', page)
    assert page.count('<tr data-id=') == 3
    assert text(client.get("/p/busqueda-de-facturas?number=000110")).count('<tr data-id=') == 1


def test_payment_in_merged_panel_updates_balance_and_status(client):
    panel = text(client.get("/p/busqueda-de-facturas?panel=registrar-pago&sel=2"))
    assert 'data-legacy-screen="REGISTRAR PAGO"' in panel and "S/ 640.00" in panel
    done = client.post("/p/registrar-pago", data={
        "parent": "2", "f_amount": "300.00", "f_date": "2026-10-26", "f_method": "TRANSFERENCIA", "f_operation": "OP-784512",
    })
    assert "number=F001-000110" in done.headers["Location"] and "sel=2" in done.headers["Location"]
    page = text(client.get(done.headers["Location"]))
    assert "OPERACION COMPLETADA SATISFACTORIAMENTE." in page
    assert page.count('<tr data-id=') == 1 and "PARCIAL" in page and "S/ 340.00" in page


def test_payment_rules_reject_bad_amounts(client):
    empty = text(client.post("/p/registrar-pago", data={"parent": "2", "f_method": "EFECTIVO"}))
    assert "ERROR 0x0042: CAMPO OBLIGATORIO" in empty and 'data-legacy-screen="REGISTRAR PAGO"' in empty
    too_much = text(client.post("/p/registrar-pago", data={"parent": "2", "f_amount": "700", "f_method": "EFECTIVO"}))
    assert "ERROR 0x00A4: MONTO EXCEDE SALDO PENDIENTE" in too_much


def test_full_payment_marks_invoice_paid(client):
    client.post("/p/registrar-pago", data={"parent": "2", "f_amount": "640", "f_method": "EFECTIVO"})
    assert "PAGADA" in text(client.get("/p/busqueda-de-facturas?number=F001-000110"))


def test_embedded_screen_redirects_to_its_page(client):
    response = client.get("/p/registrar-pago?parent=2")
    assert "/p/busqueda-de-facturas" in response.headers["Location"] and "panel=registrar-pago" in response.headers["Location"]
