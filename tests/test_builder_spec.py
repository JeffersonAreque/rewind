import pytest

from rewind.builder.runtime import expr
from rewind.builder.spec import (
    Change,
    DataModel,
    Modernization,
    ScreenSpec,
    absorbed_steps,
    check_data_model,
    check_modernization,
    check_screen,
)

# ---------- Safe expressions ----------


def test_expression_arithmetic_and_cases():
    values = {"invoice": {"balance": 64000, "total": 64000}, "payment": {"amount": 30000}}
    assert expr.evaluate("invoice.balance - payment.amount", values) == 34000
    values["invoice"]["balance"] = 34000
    assert expr.evaluate("invoice.balance > 0 and invoice.balance < invoice.total", values) is True
    assert expr.names("invoice.balance - payment.amount") == {"invoice.balance", "payment.amount"}


@pytest.mark.parametrize("text", [
    "__import__('os').system('dir')",
    "invoice.balance.__class__",
    "[x for x in invoice]",
    "lambda: 1",
    "invoice['balance']",
    "open('x')",
])
def test_expression_whitelist_rejects_code(text):
    with pytest.raises(expr.ExprError):
        expr.parse(text)


# ---------- Spec checks ----------

MODEL = DataModel.model_validate({
    "currency": "S/ ",
    "entities": [
        {"name": "customer", "label": "Cliente", "display": "business_name", "fields": [
            {"name": "code", "type": "serial", "label": "Código", "serial_prefix": "C"},
            {"name": "document", "type": "text", "label": "RUC / DNI"},
            {"name": "business_name", "type": "text", "label": "Razón social"},
        ]},
        {"name": "invoice", "label": "Factura", "display": "number", "fields": [
            {"name": "number", "type": "text", "label": "N.º"},
            {"name": "customer", "type": "ref", "label": "Cliente", "ref": "customer"},
            {"name": "balance", "type": "money", "label": "Saldo"},
            {"name": "status", "type": "enum", "label": "Estado", "enum_values": ["PENDIENTE", "PARCIAL"]},
        ]},
    ],
    "seed": [{"table": "CLIENTES", "entity": "customer",
              "columns": {"CODIGO": "code", "RUC / DNI": "document", "RAZON SOCIAL": "business_name"}}],
})

LEGACY = {
    "name": "REGISTRO DE CLIENTES",
    "sections": [{"name": "DATOS", "fields": [{"label": "RUC / DNI"}, {"label": "RAZON SOCIAL"}]}],
    "buttons": [{"label": "GRABAR"}, {"label": "LIMPIAR"}],
    "tables": [{"name": "CLIENTES", "columns": ["CODIGO", "RUC / DNI", "RAZON SOCIAL"]}],
    "menus": ["CLIENTES", "AYUDA"],
}

STEPS = [
    {"n": 1, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "GRABAR"},
    {"n": 2, "screen": "REGISTRO DE CLIENTES", "action": "see_message", "target": "ERROR 0x0042", "element": "error"},
    {"n": 3, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "ACEPTAR"},
    {"n": 4, "screen": "REGISTRO DE CLIENTES", "action": "type", "target": "RUC / DNI"},
]
RULES = [{"description": "RUC / DNI is required"}]
MOD = Modernization.model_validate({
    "app_title": "Facturación",
    "changes": [{"kind": "inline_validation", "screens": ["REGISTRO DE CLIENTES"], "legacy": "dialog",
                 "modernized": "inline", "reason": "no extra click", "evidence_steps": [2, 3]},
                {"kind": "remove_element", "screens": ["REGISTRO DE CLIENTES"], "element": "AYUDA",
                 "legacy": "AYUDA menu", "modernized": "removed", "reason": "only shows the version",
                 "evidence_steps": [1]}],
    "nav": [{"label": "Clientes", "legacy": "CLIENTES", "screen": "REGISTRO DE CLIENTES"}],
})


def screen(**changes):
    base = {
        "legacy": "REGISTRO DE CLIENTES", "kind": "create", "title": "Clientes", "entity": "customer",
        "fields": [{"name": "document", "label": "RUC / DNI", "legacy": "RUC / DNI", "input": "text"},
                   {"name": "business_name", "label": "Razón social", "legacy": "RAZON SOCIAL", "input": "text"}],
        "table": {"legacy": "CLIENTES", "label": "Clientes", "columns": [
            {"field": "code", "label": "Código", "legacy": "CODIGO"},
            {"field": "document", "label": "RUC / DNI", "legacy": "RUC / DNI"},
            {"field": "business_name", "label": "Razón social", "legacy": "RAZON SOCIAL"}]},
        "actions": [{"label": "Guardar", "legacy": "GRABAR", "action": "save"},
                    {"label": "Limpiar", "legacy": "LIMPIAR", "action": "clear"}],
        "validations": [{"field": "document", "check": "required", "code": "0x0042",
                         "message": "CAMPO OBLIGATORIO", "rule": 1}],
        "error_style": "inline", "enforces": [1],
    }
    return ScreenSpec.model_validate(base | changes)


def check(spec):
    return check_screen(spec, LEGACY, MODEL, MOD, STEPS, RULES, ["REGISTRO DE CLIENTES"])


def test_good_screen_passes():
    assert check(screen()) == []


def test_missing_legacy_field_is_reported():
    problems = check(screen(fields=[{"name": "document", "label": "RUC", "legacy": "RUC / DNI", "input": "text"}]))
    assert any("RAZON SOCIAL" in p for p in problems)


def test_unknown_entity_field_and_rule_are_reported():
    bad = screen(validations=[{"field": "document", "check": "required", "code": "42", "message": "x", "rule": 9}])
    problems = check(bad)
    assert any("rule 9" in p for p in problems) and any("0x0042" in p for p in problems)


def test_styles_must_follow_changes():
    assert any("error_style" in p for p in check(screen(error_style="dialog")))


def test_unsafe_effect_is_rejected():
    bad = screen(kind="transaction", parent="invoice", effects=[{"target": "invoice.balance", "expr": "__import__('os')"}])
    assert any("not allowed" in p for p in check(bad))


def test_data_model_checks_enum_values_against_seed():
    problems = check_data_model(MODEL, {"CLIENTES": ["CODIGO", "RUC / DNI", "RAZON SOCIAL"]}, {"CLIENTES": []})
    assert problems == []
    model = MODEL.model_copy(deep=True)
    model.seed[0].columns.pop("CODIGO")
    assert any("CODIGO" in p for p in check_data_model(model, {"CLIENTES": ["CODIGO", "RUC / DNI", "RAZON SOCIAL"]}, {}))


def test_removing_an_element_used_by_a_step_is_rejected():
    mod = MOD.model_copy(deep=True)
    mod.changes.append(Change(kind="remove_element", screens=["REGISTRO DE CLIENTES"], element="RUC / DNI",
                              legacy="x", modernized="y", reason="z", evidence_steps=[4]))
    problems = check_modernization(mod, [LEGACY], STEPS)
    assert any("used by steps [4]" in p for p in problems)


def test_absorbed_steps_follow_message_style():
    assert absorbed_steps(STEPS, {"REGISTRO DE CLIENTES": ("inline", "dialog")}) == {3: "inline_validation"}
    assert absorbed_steps(STEPS, {"REGISTRO DE CLIENTES": ("dialog", "dialog")}) == {}
