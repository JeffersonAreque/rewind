"""The app spec: everything the builder agent asks Muse Spark to fill in.

Muse Spark never writes code. It answers these pydantic models as JSON, and
the check_* functions below reject anything the fixed runtime could not run
or that would break the legacy behaviour (a missing field, a rule nobody
enforces, an unsafe expression...). Their messages go back to the model.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from rewind.agents.screens import normalize
from rewind.builder.runtime import expr

Ident = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
ERROR_CODE = re.compile(r"^0x[0-9A-F]{4}$")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(name).lower()).strip("-")


# ---------- Data model ----------


class EntityField(BaseModel):
    name: Ident
    type: Literal["text", "money", "date", "enum", "ref", "serial"] = Field(
        description="serial = code made from the record number, e.g. C0006"
    )
    label: str = Field(description="modern label, e.g. 'Razón social'")
    enum_values: list[str] = Field(default_factory=list, description="enum: every value, exactly as the app shows it")
    ref: Ident | None = Field(None, description="ref: name of the entity this field points to")
    serial_prefix: str | None = Field(None, description="serial: letters before the number, e.g. 'C'")
    serial_digits: int = 4


class Entity(BaseModel):
    name: Ident
    label: str
    fields: list[EntityField]
    display: Ident = Field(description="field shown when another record points to this one, e.g. business_name")


class SeedMapping(BaseModel):
    table: str = Field(description="legacy table name, exactly as in the screens")
    entity: Ident
    columns: dict[str, Ident | None] = Field(
        description="legacy column -> entity field; for a ref field the column holds the display value of the "
                    "other record; null = not stored"
    )


class DataModel(BaseModel):
    currency: str = Field("S/ ", description="text before money amounts, as shown in the app")
    entities: list[Entity]
    seed: list[SeedMapping] = Field(description="how each legacy table's rows become records")


# ---------- Modernization ----------


class Change(BaseModel):
    kind: Literal[
        "merge_screens", "rename_label", "inline_validation", "remove_dialog_step",
        "default_value", "reorder_fields", "remove_element",
    ]
    screens: list[str] = Field(description="legacy screen names involved; merge_screens: [search screen, screen it opens]")
    element: str | None = Field(None, description="legacy label of the field, button, column or menu, if any")
    legacy: str = Field(description="how it was")
    modernized: str = Field(description="how it will be")
    reason: str
    evidence_steps: list[int] = Field(description="numbers of the steps in the recording that justify it")


class NavItem(BaseModel):
    label: str = Field(description="modern label")
    legacy: str = Field(description="legacy menu label")
    screen: str = Field(description="legacy name of the screen it opens")


class Modernization(BaseModel):
    app_title: str
    changes: list[Change]
    nav: list[NavItem]


# ---------- Screens ----------


class UIField(BaseModel):
    name: Ident = Field(description="entity field name (login: username / password)")
    label: str
    legacy: str = Field(description="legacy label, exactly as in the screens")
    input: Literal["text", "password", "select", "radio", "date", "money", "readonly"]
    default: str | None = Field(None, description="'today' or a fixed value")


class Filter(BaseModel):
    name: Ident
    label: str
    legacy: str
    field: Ident = Field(description="entity field it filters")
    op: Literal["eq", "contains", "from", "to"]
    input: Literal["text", "select", "date"]


class Column(BaseModel):
    field: str = Field(description="entity field, or ref.field for a linked record (e.g. customer.business_name)")
    label: str
    legacy: str


class Table(BaseModel):
    legacy: str = Field(description="legacy table name")
    label: str
    columns: list[Column]
    selectable: bool = False


class Action(BaseModel):
    label: str
    legacy: str = Field(description="legacy button label")
    action: Literal["login", "save", "clear", "search", "lookup", "open", "cancel"]
    target: str | None = Field(None, description="open / cancel: legacy name of the screen it goes to")
    requires_selection: bool = Field(False, description="enabled only after a table row is selected")


class Validation(BaseModel):
    field: Ident
    check: Literal["required", "positive", "not_above"]
    other: str | None = Field(None, description="not_above: the entity.field it cannot exceed")
    code: str = Field(description="legacy error code, e.g. 0x0042")
    message: str = Field(description="legacy error text, e.g. CAMPO OBLIGATORIO")
    rule: int = Field(description="number of the business rule this comes from")


class Case(BaseModel):
    when: str = Field(description="safe expression, e.g. invoice.balance == 0")
    value: str


class Effect(BaseModel):
    target: str = Field(description="parent entity.field to update, e.g. invoice.balance")
    expr: str | None = Field(None, description="safe expression, e.g. invoice.balance - payment.amount")
    cases: list[Case] = Field(default_factory=list, description="for a status: first case that holds wins")
    default: str | None = None


class Success(BaseModel):
    message: str = Field(description="legacy success message")
    goto: str | None = Field(None, description="legacy name of the screen shown after saving")
    with_parent: bool = Field(False, description="show that screen filtered on the updated parent record")


class ScreenSpec(BaseModel):
    legacy: str
    kind: Literal["login", "create", "search", "transaction"]
    title: str
    entity: Ident | None = Field(None, description="create: record created; search: record searched; transaction: child record")
    parent: Ident | None = Field(None, description="transaction: record shown and updated")
    parent_lookup: Ident | None = Field(None, description="transaction: parent field typed to look the record up")
    fields: list[UIField] = Field(default_factory=list, description="form fields")
    shown: list[UIField] = Field(default_factory=list, description="transaction: parent fields shown (read-only, plus lookup)")
    filters: list[Filter] = Field(default_factory=list)
    table: Table | None = None
    actions: list[Action] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    success: Success | None = None
    error_style: Literal["dialog", "inline"] = "dialog"
    success_style: Literal["dialog", "toast"] = "dialog"
    embedded_in: str | None = Field(None, description="merge_screens: legacy name of the search screen that hosts this one")
    enforces: list[int] = Field(default_factory=list, description="numbers of the business rules this screen enforces")


# ---------- Checks ----------


def legacy_elements(screen: dict) -> dict[str, set[str]]:
    """Normalized legacy labels of one screens.json screen, by kind."""
    return {
        "fields": {normalize(f["label"]) for s in screen["sections"] for f in s["fields"]},
        "buttons": {normalize(b["label"]) for b in screen["buttons"]},
        "columns": {normalize(c) for t in screen["tables"] for c in t["columns"]},
        "tables": {normalize(t["name"]) for t in screen["tables"]},
        "menus": {normalize(m) for m in screen["menus"]},
    }


def removed(changes: list[Change], screen: str) -> set[str]:
    return {normalize(c.element) for c in changes
            if c.kind == "remove_element" and c.element and normalize(screen) in map(normalize, c.screens)}


def check_data_model(model: DataModel, tables: dict[str, list[str]], rows: dict[str, list[list[str]]]) -> list[str]:
    """tables: legacy table -> columns; rows: legacy table -> transcribed rows."""
    problems = []
    entities = {e.name: e for e in model.entities}
    if len(entities) != len(model.entities):
        problems.append("entity names must be unique")
    for e in model.entities:
        names = [f.name for f in e.fields]
        if len(set(names)) != len(names):
            problems.append(f"entity {e.name}: field names must be unique")
        if e.display not in names:
            problems.append(f"entity {e.name}: display field {e.display!r} is not one of its fields")
        for f in e.fields:
            if f.type == "ref" and f.ref not in entities:
                problems.append(f"{e.name}.{f.name}: ref must name an entity, got {f.ref!r}")
            if f.type == "enum" and not f.enum_values:
                problems.append(f"{e.name}.{f.name}: enum needs enum_values")
            if f.type == "serial" and not f.serial_prefix:
                problems.append(f"{e.name}.{f.name}: serial needs serial_prefix")
    mapped = {normalize(m.table): m for m in model.seed}
    for table, columns in tables.items():
        mapping = mapped.get(normalize(table))
        if not mapping:
            problems.append(f"table {table!r} has no seed mapping")
            continue
        entity = entities.get(mapping.entity)
        if not entity:
            problems.append(f"seed of {table!r}: unknown entity {mapping.entity!r}")
            continue
        fields = {f.name: f for f in entity.fields}
        given = {normalize(c): field for c, field in mapping.columns.items()}
        for i, column in enumerate(columns):
            if normalize(column) not in given:
                problems.append(f"seed of {table!r}: column {column!r} is not mapped (use null to skip it)")
                continue
            field = fields.get(given[normalize(column)]) if given[normalize(column)] else None
            if given[normalize(column)] and not field:
                problems.append(f"seed of {table!r}: column {column!r} maps to unknown field {given[normalize(column)]!r}")
            elif field and field.type == "enum":
                values = {normalize(v) for v in field.enum_values}
                for row in rows.get(table, []):
                    if i < len(row) and row[i] and normalize(row[i]) not in values:
                        problems.append(f"{entity.name}.{field.name}: value {row[i]!r} from {table!r} is not in enum_values")
    return problems


def check_modernization(mod: Modernization, screens: list[dict], steps: list[dict]) -> list[str]:
    problems = []
    by_name = {normalize(s["name"]): s for s in screens}
    numbers = {s["n"] for s in steps}
    menus = set().union(*(legacy_elements(s)["menus"] for s in screens))
    for i, change in enumerate(mod.changes, 1):
        where = f"change {i} ({change.kind})"
        if bad := [n for n in change.evidence_steps if n not in numbers]:
            problems.append(f"{where}: evidence steps {bad} do not exist")
        if not change.evidence_steps:
            problems.append(f"{where}: give the steps that justify it")
        unknown = [s for s in change.screens if normalize(s) not in by_name]
        if unknown or not change.screens:
            problems.append(f"{where}: unknown screens {unknown or change.screens}")
            continue
        if change.kind == "merge_screens":
            if len(change.screens) != 2:
                problems.append(f"{where}: name exactly 2 screens: the search screen and the screen it opens")
            elif not opened_from(steps, *change.screens):
                problems.append(f"{where}: the steps never go from {change.screens[0]!r} to {change.screens[1]!r}")
        if change.kind in ("rename_label", "remove_element", "default_value") and change.element:
            labels = set().union(*legacy_elements(by_name[normalize(change.screens[0])]).values())
            if normalize(change.element) not in labels:
                problems.append(f"{where}: {change.element!r} is not on {change.screens[0]!r}")
        if change.kind == "remove_element" and change.element:
            used = [s["n"] for s in steps if normalize(s["target"]) == normalize(change.element)
                    and normalize(s["screen"]) in map(normalize, change.screens) and s["action"] != "open_screen"]
            if used:
                problems.append(f"{where}: {change.element!r} is used by steps {used}, it cannot be removed")
    for item in mod.nav:
        if normalize(item.screen) not in by_name:
            problems.append(f"nav {item.label!r}: unknown screen {item.screen!r}")
    covered = {normalize(n.legacy) for n in mod.nav} | {
        normalize(c.element) for c in mod.changes if c.kind == "remove_element" and c.element
    }
    if missing := sorted(menus - covered):
        problems.append(f"menus {missing} need a nav item or a remove_element change")
    return problems


def opened_from(steps: list[dict], source: str, target: str) -> bool:
    """True when some click on `source` is followed by open_screen `target`."""
    for before, after in zip(steps, steps[1:]):
        if (before["action"] == "click" and normalize(before["screen"]) == normalize(source)
                and after["action"] == "open_screen" and normalize(after["target"]) == normalize(target)):
            return True
    return False


def absorbed_steps(steps: list[dict], styles: dict[str, tuple[str, str]]) -> dict[int, str]:
    """Steps that a modern screen makes unnecessary: the click that closes a message
    which is now shown inline or as a toast. styles: legacy screen -> (error, success)."""
    absorbed = {}
    for before, step in zip(steps, steps[1:]):
        if before["action"] == "see_message" and step["action"] == "click":
            error_style, success_style = styles.get(normalize(before["screen"]), ("dialog", "dialog"))
            style = error_style if before.get("element") == "error" else success_style
            if style != "dialog":
                kind = "inline_validation" if style == "inline" else "remove_dialog_step"
                absorbed[step["n"]] = kind
    return absorbed


def check_screen(spec: ScreenSpec, screen: dict, model: DataModel, mod: Modernization,
                 steps: list[dict], rules: list[dict], screen_names: list[str]) -> list[str]:
    """Everything a screen spec must satisfy before the runtime can use it."""
    problems = []
    entities = {e.name: {f.name: f for f in e.fields} for e in model.entities}
    known_screens = {normalize(n) for n in screen_names}
    if normalize(spec.legacy) != normalize(screen["name"]):
        problems.append(f"legacy must be {screen['name']!r}")

    def need_entity(name, role):
        if name not in entities:
            problems.append(f"{role} {name!r} is not an entity")
            return {}
        return entities[name]

    own = need_entity(spec.entity, "entity") if spec.kind != "login" else {}
    parent = need_entity(spec.parent, "parent") if spec.kind == "transaction" else {}

    # Fields refer to real entity fields.
    if spec.kind == "login":
        if {f.name for f in spec.fields} != {"username", "password"}:
            problems.append("login fields must be named username and password")
    for f in spec.fields if spec.kind != "login" else []:
        if f.name not in own:
            problems.append(f"field {f.legacy!r}: {f.name!r} is not a field of {spec.entity}")
    for f in spec.shown:
        if f.name not in parent:
            problems.append(f"shown {f.legacy!r}: {f.name!r} is not a field of {spec.parent}")
    if spec.kind == "transaction" and spec.parent_lookup and spec.parent_lookup not in parent:
        problems.append(f"parent_lookup {spec.parent_lookup!r} is not a field of {spec.parent}")
    for f in spec.filters:
        if f.field not in own:
            problems.append(f"filter {f.legacy!r}: {f.field!r} is not a field of {spec.entity}")
    if spec.table:
        for c in spec.table.columns:
            base, _, sub = c.field.partition(".")
            if base not in own:
                problems.append(f"column {c.legacy!r}: {base!r} is not a field of {spec.entity}")
            elif sub:
                target = own[base].ref
                if own[base].type != "ref" or sub not in entities.get(target, {}):
                    problems.append(f"column {c.legacy!r}: {c.field!r} is not ref.field")

    # Every legacy element is kept or removed on purpose.
    gone = removed(mod.changes, spec.legacy)
    have = {
        "fields": {normalize(f.legacy) for f in [*spec.fields, *spec.shown, *spec.filters]},
        "buttons": {normalize(a.legacy) for a in spec.actions},
        "columns": {normalize(c.legacy) for c in spec.table.columns} if spec.table else set(),
        "tables": {normalize(spec.table.legacy)} if spec.table else set(),
    }
    legacy = legacy_elements(screen)
    for kind, labels in have.items():
        if missing := sorted(legacy[kind] - labels - gone):
            problems.append(f"legacy {kind} {missing} are missing (keep them or remove them with a remove_element change)")

    # Rules, validations and expressions.
    rule_numbers = set(range(1, len(rules) + 1))
    if bad := [n for n in spec.enforces if n not in rule_numbers]:
        problems.append(f"enforces rules {bad}, which do not exist (rules are 1-{len(rules)})")
    form = {f.name for f in spec.fields}
    for v in spec.validations:
        if v.field not in form:
            problems.append(f"validation on {v.field!r}: not a form field of this screen")
        if v.rule not in rule_numbers:
            problems.append(f"validation on {v.field!r}: rule {v.rule} does not exist")
        if not ERROR_CODE.match(v.code):
            problems.append(f"validation on {v.field!r}: code {v.code!r} must look like 0x0042")
        if v.check == "not_above" and not v.other:
            problems.append(f"validation on {v.field!r}: not_above needs other")
    scope = {f"{spec.entity}.{n}" for n in own} | {f"{spec.parent}.{n}" for n in parent}
    for effect in spec.effects:
        if effect.target not in {f"{spec.parent}.{n}" for n in parent}:
            problems.append(f"effect target {effect.target!r} must be {spec.parent}.<field>")
        if bool(effect.expr) == bool(effect.cases):
            problems.append(f"effect on {effect.target!r}: give either expr or cases")
        for text in [effect.expr] if effect.expr else [c.when for c in effect.cases]:
            try:
                if unknown := sorted(expr.names(text) - scope):
                    problems.append(f"expression {text!r} uses unknown names {unknown}")
            except expr.ExprError as exc:
                problems.append(str(exc))

    # Links to other screens.
    for a in spec.actions:
        if a.action in ("open", "cancel") and normalize(a.target or "") not in known_screens:
            problems.append(f"button {a.legacy!r}: target {a.target!r} is not a legacy screen")
    if spec.success and spec.success.goto and normalize(spec.success.goto) not in known_screens:
        problems.append(f"success goto {spec.success.goto!r} is not a legacy screen")

    # Styles follow the modernization changes.
    mine = [c for c in mod.changes if normalize(spec.legacy) in map(normalize, c.screens)]
    kinds = {c.kind for c in mine}
    if ("inline_validation" in kinds) != (spec.error_style == "inline"):
        problems.append("error_style must be 'inline' exactly when an inline_validation change names this screen")
    if ("remove_dialog_step" in kinds) != (spec.success_style == "toast"):
        problems.append("success_style must be 'toast' exactly when a remove_dialog_step change names this screen")
    hosts = [c.screens[0] for c in mine if c.kind == "merge_screens" and normalize(c.screens[-1]) == normalize(spec.legacy)]
    if normalize(spec.embedded_in or "") != normalize(hosts[0] if hosts else ""):
        problems.append(f"embedded_in must be {hosts[0]!r}" if hosts else "embedded_in must be null")

    # Every step on this screen can be replayed.
    targets = have["fields"] | have["buttons"] | have["tables"] | {normalize(n.legacy) for n in mod.nav}
    for step in steps:
        if normalize(step["screen"]) != normalize(spec.legacy) or step["action"] not in ("type", "select", "click"):
            continue
        dialog_button = step["action"] == "click" and normalize(step["target"]) in {"ACEPTAR", "OK", "CANCELAR"}
        if normalize(step["target"]) not in targets and not dialog_button:
            problems.append(f"step {step['n']} ({step['action']} {step['target']}) has no element on this screen")
    return problems


def check_rules_enforced(screens: list[ScreenSpec], rules: list[dict]) -> list[str]:
    claimed = {n for s in screens for n in s.enforces}
    return [f"rule {n} ({rules[n - 1]['description']!r}) is enforced by no screen"
            for n in range(1, len(rules) + 1) if n not in claimed]
