"""The fixed runtime, driven by the hand-written demo spec (no model calls)."""

import copy
import importlib.util
from contextlib import closing
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
    assert text(client.get("/p/busqueda-de-facturas")).count('<tr data-id=') == 0  # empty until a search runs
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


def test_error_text_is_shown_as_the_legacy_app_showed_it(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["screens"][1]["validations"][0] |= {"code": "E-101", "message": "E-101 DATO REQUERIDO: RUC / DNI"}
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    page = text(c.post("/p/registro-de-clientes", data={}))
    assert "E-101 DATO REQUERIDO: RUC / DNI" in page and "ERROR E-101" not in page


def test_a_filter_can_start_with_today_and_still_waits_for_a_search(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_TODAY", "2026-10-26")
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["screens"][2]["filters"][3]["default"] = "today"
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    page = text(c.get("/p/busqueda-de-facturas"))
    assert 'value="2026-10-26"' in page and page.count('<tr data-id=') == 0


def test_formulas_fill_joined_fields_and_filters_follow_links(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    customer = next(e for e in spec["entities"] if e["name"] == "customer")
    customer["fields"].append({"name": "label", "type": "text", "label": "Etiqueta",
                               "formula": "customer.document + ' - ' + customer.business_name"})
    search = spec["screens"][2]
    search["filters"].append({"name": "doc", "label": "RUC", "legacy": "RUC", "field": "customer.document",
                              "op": "eq", "input": "text"})
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    c.post("/p/registro-de-clientes", data={"f_document": "20612345678", "f_business_name": "FERRETERIA X",
                                            "f_address": "JR. LORETO 455"})
    with closing(runtime.connect(tmp_path / "app.db")) as db:
        assert db.execute("SELECT label FROM customer WHERE document = '20612345678'").fetchone()[0] == \
            "20612345678 - FERRETERIA X"
    tambopata = FIXTURE["seed"]["customer"][0]["document"]
    assert text(c.get(f"/p/busqueda-de-facturas?doc={tambopata}")).count("<tr data-id=") == 3


def test_a_merged_panel_goes_back_to_the_same_search(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["screens"][3]["success"]["with_parent"] = False
    spec["screens"][3]["actions"].append({"label": "Cancelar", "legacy": "CANCELAR", "action": "cancel",
                                          "target": "busqueda-de-facturas"})
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    page = c.get("/p/busqueda-de-facturas?panel=registrar-pago&sel=2&customer=1&status=PENDIENTE").get_data(as_text=True)
    cancel = re.search(r'href="([^"]+)" data-legacy="CANCELAR"', page).group(1)
    assert "customer=1" in cancel and "status=PENDIENTE" in cancel and "panel" not in cancel

    search = {"customer": "1", "status": "PENDIENTE"}
    refused = text(c.post("/p/registrar-pago", data={"parent": "2", "f_amount": "", "f_method": "EFECTIVO"} | search))
    assert "0x0042" in refused and refused.count("<tr data-id=") == 3  # the results stay behind the error
    done = c.post("/p/registrar-pago", data={"parent": "2", "f_amount": "100", "f_method": "EFECTIVO"} | search)
    assert "customer=1" in done.headers["Location"] and "status=PENDIENTE" in done.headers["Location"]


def test_a_condition_refuses_a_save_in_the_wrong_state(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["screens"][3]["validations"].append({"field": "amount", "check": "condition", "code": "E-305",
                                              "other": "invoice.status == 'PENDIENTE'",
                                              "message": "E-305 SOLO FACTURAS PENDIENTES", "rule": 1})
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    payment = {"parent": "2", "f_amount": "100", "f_method": "EFECTIVO"}
    assert c.post("/p/registrar-pago", data=payment).status_code == 302  # PENDIENTE: saved, now PARCIAL
    assert "E-305 SOLO FACTURAS PENDIENTES" in text(c.post("/p/registrar-pago", data=payment))


def test_a_list_over_a_text_field_offers_the_stored_values(tmp_path, monkeypatch):
    spec = copy.deepcopy(FIXTURE["spec"])
    number = next(f for f in spec["screens"][2]["filters"] if f["field"] == "number")
    number["input"] = "select"
    c = make_client(tmp_path, monkeypatch, spec)
    c.post("/login", data={"username": "ADMIN", "password": PASSWORD})
    assert '<option value="F001-000110"' in text(c.get("/p/busqueda-de-facturas"))


def test_today_can_be_pinned_to_the_day_of_the_recording(client, monkeypatch):
    monkeypatch.setenv("APP_TODAY", "2026-10-26")
    assert 'value="2026-10-26"' in text(client.get("/p/busqueda-de-facturas?panel=registrar-pago&sel=2"))


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
