import ast
import json

from rewind.builder.testgen import module_source, observation_checks, replay_steps, write_tests
from rewind.config import ROOT

SPEC = json.loads((ROOT / "samples" / "facturacion-demo.spec.json").read_text(encoding="utf-8"))["spec"]


def step(n, screen, action, target, frame, **extra):
    return {"n": n, "screen": screen, "action": action, "target": target,
            "frame_from": frame, "frame_to": frame + 1, "time_from": frame, "time_to": frame + 1} | extra


FLOWS = {
    "steps": [
        step(1, "REGISTRO DE CLIENTES", "click", "GRABAR", 0),
        step(2, "REGISTRO DE CLIENTES", "see_message", "ERROR 0x0042: CAMPO OBLIGATORIO", 0, element="error", code="0x0042"),
        step(3, "REGISTRO DE CLIENTES", "click", "ACEPTAR", 1),
        step(4, "REGISTRAR PAGO", "click", "GRABAR", 2),
        step(5, "BUSQUEDA DE FACTURAS", "see_message", "OPERACION COMPLETADA SATISFACTORIAMENTE.", 2, element="info"),
        step(6, "BUSQUEDA DE FACTURAS", "click", "ACEPTAR", 3),
    ],
    "observations": [
        {"from": 2, "to": 3, "subject": "ESTADO of invoice F001-000110", "before": "PENDIENTE", "after": "PARCIAL"},
        {"from": 2, "to": 3, "subject": "status bar", "before": "LISTO", "after": "1 REGISTROS"},
        {"from": 0, "to": 1, "subject": "CLIENTES REGISTRADOS rows", "before": "6", "after": "6"},
    ],
    "workflows": [
        {"name": "Register customer", "goal": 'Create a "new" customer', "steps": [1, 2, 3]},
        {"name": "Register payment", "goal": "Pay", "steps": [4, 5, 6]},
    ],
}


def test_observations_become_checks():
    assert observation_checks({"subject": "status bar", "before": "a", "after": "b"}) == []
    assert observation_checks({"subject": "RESULTADOS rows", "before": "0 rows", "after": "3 rows"}) == [
        {"kind": "row_count", "table": "RESULTADOS", "count": 3}]
    button = observation_checks({"subject": "REGISTRAR PAGO button", "before": "disabled", "after": "enabled"})
    assert [(c["enabled"], c.get("when")) for c in button] == [(False, "before"), (True, None)]
    row = observation_checks({"subject": "row C0007", "before": "", "after": "C0007 | 2061 | FERRETERIA"})
    assert row == [{"kind": "text", "texts": ["C0007", "2061", "FERRETERIA"]}]


def test_dialog_clicks_are_absorbed_by_the_screen_that_made_the_message():
    steps = {s["n"]: s for s in replay_steps(FLOWS, SPEC)}
    assert steps[3]["absorbed"].startswith("inline_validation")
    assert steps[6]["absorbed"].startswith("remove_dialog_step")  # payment toast, shown on the search page
    assert "absorbed" not in steps[1]
    assert steps[5]["after"] == [{"kind": "text", "texts": ["PARCIAL"]}]
    assert steps[2]["after"] == [{"kind": "row_count", "table": "CLIENTES REGISTRADOS", "count": 6}]


def test_generated_module_is_valid_python(tmp_path):
    write_tests(tmp_path, FLOWS, SPEC)
    source = (tmp_path / "tests" / "test_workflows.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [f.name for f in tree.body if isinstance(f, ast.FunctionDef)]
    assert names == ["test_register_customer", "test_register_payment"]
    assert "first=4, last=6" in source
    assert (tmp_path / "tests" / "replay.py").exists() and (tmp_path / "tests" / "conftest.py").exists()


def test_starting_values_are_checked_when_the_screen_first_opens():
    flows = FLOWS | {"steps": [step(1, "REGISTRAR PAGO", "open_screen", "REGISTRAR PAGO", 0)] + FLOWS["steps"][3:]}
    defaults = {"REGISTRAR PAGO": {"METODO": "EFECTIVO", "FECHA PAGO": "26/10/2026"}}
    first = replay_steps(flows, SPEC, defaults)[0]
    assert {"kind": "default", "field": "METODO", "value": "EFECTIVO"} in first["after"]
    assert {"kind": "default", "field": "FECHA PAGO", "value": "today"} in first["after"]


def test_duplicate_workflow_names_get_unique_tests():
    flows = FLOWS | {"workflows": [{"name": "Pay", "steps": [1]}, {"name": "Pay", "steps": [2]}]}
    source = module_source(flows, replay_steps(flows, SPEC))
    assert "def test_pay(" in source and "def test_pay_2(" in source
