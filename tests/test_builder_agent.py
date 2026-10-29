"""Builder agent pieces that need no model: seed records, assembly and the changes report."""

import json

from rewind.agents.builder import as_built, assemble, first_tables, seed_records
from rewind.agents.screens import FrameAnalysis, Table
from rewind.builder.spec import DataModel, Modernization, ScreenSpec
from rewind.config import ROOT

FIXTURE = json.loads((ROOT / "samples" / "facturacion-demo.spec.json").read_text(encoding="utf-8"))
SPEC = FIXTURE["spec"]
MODEL = DataModel.model_validate({
    "currency": SPEC["currency"],
    "entities": SPEC["entities"],
    "seed": [
        {"table": "CLIENTES REGISTRADOS", "entity": "customer",
         "columns": {"CODIGO": "code", "RUC / DNI": "document", "RAZON SOCIAL": "business_name"}},
        {"table": "RESULTADOS", "entity": "invoice",
         "columns": {"NRO. FACTURA": "number", "CLIENTE": "customer", "ESTADO": "status"}},
    ],
})


def frame(index, tables):
    analysis = FrameAnalysis(screen_name="X", window_title="X", tables=[Table(**t) for t in tables])
    return {"index": index, "time": float(index), "image": f"frames/{index}.png", "analysis": analysis}


def test_first_frame_with_rows_is_the_starting_data():
    frames = [
        frame(0, [{"name": "RESULTADOS", "columns": ["A"], "rows": 0}]),
        frame(1, [{"name": "RESULTADOS", "columns": ["A"], "rows": 3}]),
        frame(2, [{"name": "RESULTADOS", "columns": ["A"], "rows": 1}]),
    ]
    found = first_tables(frames)
    assert list(found) == ["RESULTADOS"] and found["RESULTADOS"][0]["index"] == 1


def test_seed_records_follow_the_serial_order_and_skip_serials():
    tables = {"CLIENTES REGISTRADOS": ["CODIGO", "RUC / DNI", "RAZON SOCIAL"],
              "RESULTADOS": ["NRO. FACTURA", "CLIENTE", "ESTADO"]}
    rows = {"CLIENTES REGISTRADOS": [["C0002", "222", "B S.A."], ["C0001", "111", "A S.A."]],
            "RESULTADOS": [["F001-000110", "A S.A.", "PENDIENTE"]]}
    seed = seed_records(MODEL, tables, rows)
    assert seed["customer"] == [{"document": "111", "business_name": "A S.A."},
                                {"document": "222", "business_name": "B S.A."}]
    assert seed["invoice"] == [{"number": "F001-000110", "customer": "A S.A.", "status": "PENDIENTE"}]
    assert seed["payment"] == []


def test_rows_of_a_search_share_its_filters_and_named_records_are_created():
    model = DataModel.model_validate({"entities": [
        {"name": "doctor", "label": "Médico", "display": "name", "fields": [{"name": "name", "type": "text", "label": "Nombre"}]},
        {"name": "slot", "label": "Turno", "display": "time", "fields": [
            {"name": "time", "type": "text", "label": "Hora"}, {"name": "day", "type": "date", "label": "Fecha"},
            {"name": "doctor", "type": "ref", "label": "Médico", "ref": "doctor"}]},
    ], "seed": [{"table": "TURNOS", "entity": "slot", "columns": {"HORA": "time", "MEDICO": "doctor"}}]})
    agenda = ScreenSpec.model_validate({
        "legacy": "AGENDA", "kind": "search", "title": "Agenda", "entity": "slot",
        "filters": [{"name": "day", "label": "Fecha", "legacy": "FECHA", "field": "day", "op": "eq", "input": "date"}],
        "table": {"legacy": "TURNOS", "label": "Turnos", "columns": [], "selectable": True}})
    steps = [
        {"n": 1, "screen": "AGENDA", "action": "open_screen", "target": "AGENDA", "frame_to": 1},
        {"n": 2, "screen": "AGENDA", "action": "type", "target": "FECHA", "value": "29/10/2026", "frame_to": 2},
        {"n": 3, "screen": "AGENDA", "action": "click", "target": "CONSULTAR", "frame_to": 3},
        {"n": 4, "screen": "AGENDA", "action": "type", "target": "FECHA", "value": "30/10/2026", "frame_to": 5},
    ]
    from rewind.agents.builder import search_values
    shared = {"TURNOS": search_values("TURNOS", 3, steps, [agenda])}
    assert shared == {"TURNOS": {"day": "29/10/2026"}}  # the later search does not count

    rows = {"TURNOS": [["08:00", "DRA. VARGAS"], ["08:30", "DR. SALINAS"], ["09:00", "DRA. VARGAS"]]}
    seed = seed_records(model, {"TURNOS": ["HORA", "MEDICO"]}, rows, shared)
    assert seed["slot"][0] == {"day": "29/10/2026", "time": "08:00", "doctor": "DRA. VARGAS"}
    assert seed["doctor"] == [{"name": "DRA. VARGAS"}, {"name": "DR. SALINAS"}]

    # A filter through the link (ESPECIALIDAD = doctor.specialty) describes the linked records.
    shared["TURNOS"]["doctor.specialty"] = "PEDIATRIA"
    seed = seed_records(model, {"TURNOS": ["HORA", "MEDICO"]}, rows, shared)
    assert seed["doctor"] == [{"name": "DRA. VARGAS", "specialty": "PEDIATRIA"},
                              {"name": "DR. SALINAS", "specialty": "PEDIATRIA"}]


def legacy_screens():
    """The fixture's screens as the model returns them: legacy names instead of page ids."""
    names = {s["id"]: s["legacy"] for s in SPEC["screens"]}
    out = []
    for s in SPEC["screens"]:
        data = {k: v for k, v in s.items() if k != "id"}
        data["actions"] = [a | {"target": names.get(a.get("target"), a.get("target"))} for a in s["actions"]]
        if data.get("embedded_in"):
            data["embedded_in"] = names[data["embedded_in"]]
        if data.get("success") and data["success"].get("goto"):
            data["success"] = data["success"] | {"goto": names[data["success"]["goto"]]}
        out.append(ScreenSpec.model_validate(data))
    return out


def test_assemble_turns_legacy_names_into_page_ids():
    flows = {"steps": [
        {"n": 1, "screen": "INICIO DE SESION", "action": "open_screen", "target": "INICIO DE SESION"},
        {"n": 2, "screen": "INICIO DE SESION", "action": "type", "target": "USUARIO", "value": "ADMIN"},
        {"n": 3, "screen": "REGISTRO DE CLIENTES", "action": "open_screen", "target": "REGISTRO DE CLIENTES"},
    ]}
    mod = Modernization(app_title="Demo", changes=[], nav=[{"label": "Clientes", "legacy": "CLIENTES",
                                                           "screen": "REGISTRO DE CLIENTES"}])
    app = assemble(MODEL, mod, legacy_screens(), flows)
    assert app["home"] == "registro-de-clientes" and app["users"] == ["ADMIN"]
    pay = next(s for s in app["screens"] if s["id"] == "registrar-pago")
    assert pay["embedded_in"] == "busqueda-de-facturas" and pay["success"]["goto"] == "busqueda-de-facturas"
    assert app["nav"][0]["screen"] == "registro-de-clientes"


def test_changes_are_described_from_what_was_built():
    change = {"kind": "rename_label", "screens": ["REGISTRO DE CLIENTES"], "element": "GRABAR",
              "legacy": "model text", "modernized": "model claims a tooltip", "reason": "", "evidence_steps": [6]}
    assert as_built(change, SPEC) == ("«GRABAR»", "«Guardar cliente»")
    merge = {"kind": "merge_screens", "screens": ["BUSQUEDA DE FACTURAS", "REGISTRAR PAGO"], "legacy": "",
             "modernized": "", "reason": "", "evidence_steps": [21]}
    assert "side panel" in as_built(merge, SPEC)[1]
