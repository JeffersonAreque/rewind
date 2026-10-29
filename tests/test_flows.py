from rewind.agents.flows import (
    Action,
    Observation,
    PairAnalysis,
    Synthesis,
    analyze_pair,
    build_steps,
    check_synthesis,
    cross_check,
    describe,
    synthesis_input,
)
from rewind.agents.screens import Button, Dialog, FrameAnalysis, Section, UIField
from tests.fakes import FakeClient

PAIR = '{"actions": [{"action": "click", "target": "GRABAR", "element": "button"}], "observations": []}'


def make_frame(index, time, screen, fields=None, dialog=None, status=(), buttons=()):
    """A frame as Agent 1 leaves it in frames.json, with an already parsed analysis."""
    analysis = FrameAnalysis(
        screen_name=screen,
        window_title=f"APP - [{screen}]",
        sections=[Section(name="DATOS", fields=[
            UIField(label=label, value=value, editable=True) for label, value in (fields or {}).items()
        ])],
        buttons=[Button(label=b) for b in buttons],
        dialog=dialog,
        status_bar=list(status),
    )
    return {"index": index, "time": time, "image": f"frames/{index}.png", "analysis": analysis}


def test_describe_shares_agent1_vocabulary():
    error = Dialog(kind="error", title="ERROR", message="ERROR 0x0042: CAMPO OBLIGATORIO", buttons=["ACEPTAR"])
    text = describe(make_frame(2, 9.1, "REGISTRO DE CLIENTES", {"RUC / DNI": ""}, error, ["USUARIO: ADMIN"], ["GRABAR"]))
    assert "screen: REGISTRO DE CLIENTES" in text
    assert "RUC / DNI=''" in text
    assert "ERROR 0x0042" in text and "USUARIO: ADMIN" in text and "GRABAR" in text


def test_pair_is_cached(tmp_path):
    (tmp_path / "frames").mkdir()
    for i in (0, 1):
        (tmp_path / "frames" / f"{i}.png").write_bytes(b"png%d" % i)
    before = make_frame(0, 1.0, "CLIENTES")
    after = make_frame(1, 2.0, "CLIENTES", dialog=Dialog(kind="info", title="OK", message="OPERACION COMPLETADA"))
    client = FakeClient(PAIR)

    first = analyze_pair(client, "model", before, after, tmp_path, tmp_path / "cache")
    second = analyze_pair(client, "model", before, after, tmp_path, tmp_path / "cache")

    assert first["analysis"].actions[0].target == "GRABAR"
    assert second["cached"] and len(client.calls) == 1


def pair(i, j, *actions):
    analysis = PairAnalysis(actions=[Action(**a) for a in actions])
    return {"from": i, "to": j, "time_from": float(i), "time_to": float(j), "analysis": analysis}


def test_steps_add_screen_changes_and_messages():
    error = Dialog(kind="error", title="ERROR", message="ERROR 0x0042: CAMPO OBLIGATORIO")
    frames = [
        make_frame(0, 0.0, "INICIO DE SESION"),
        make_frame(1, 1.0, "CLIENTES"),
        make_frame(2, 2.0, "CLIENTES", dialog=error),
    ]
    pairs = [
        pair(0, 1, {"action": "click", "target": "ACEPTAR", "element": "button"}),
        pair(1, 2, {"action": "click", "target": "GRABAR", "element": "button"}),
    ]
    steps = build_steps(frames, pairs)
    assert [(s.n, s.action, s.target) for s in steps] == [
        (1, "open_screen", "INICIO DE SESION"),
        (2, "click", "ACEPTAR"),
        (3, "open_screen", "CLIENTES"),
        (4, "click", "GRABAR"),
        (5, "see_message", "ERROR 0x0042: CAMPO OBLIGATORIO"),
    ]
    assert steps[1].screen == "INICIO DE SESION"  # the click happened on the login screen
    assert steps[4].code == "0x0042" and steps[4].element == "error"


def test_values_typed_before_the_first_keyframe_are_steps():
    first = make_frame(0, 2.8, "ACCESO", fields={"OPERADOR": "RECEPCION", "CONTRASENA": "******", "FECHA": "DD/MM/AAAA"})
    first["analysis"].sections[0].fields[1].type = "password"
    later = make_frame(1, 4.0, "AGENDA")
    steps = build_steps([first, later], [pair(0, 1, {"action": "click", "target": "INGRESAR", "element": "button"})])
    assert [(s.action, s.target, s.value) for s in steps[:4]] == [
        ("open_screen", "ACCESO", None), ("type", "OPERADOR", "RECEPCION"), ("type", "CONTRASENA", None),
        ("click", "INGRESAR", None)]
    assert not steps[2].value_visible


def test_error_codes_in_the_shapes_old_programs_use():
    from rewind.agents.flows import ERROR_CODE

    for message, code in [("ERROR 0x0042: CAMPO OBLIGATORIO", "0x0042"), ("E-101 DATO REQUERIDO: DNI", "E-101"),
                          ("ORA-00001: UNIQUE CONSTRAINT", "ORA-00001"), ("SQL0803 DUPLICADO", "SQL0803")]:
        assert ERROR_CODE.search(message).group(0) == code
    for message in ("FACTURA F001-000110 NO EXISTE", "FECHA 29/10/2026 INVALIDA", "TURNO 09:30 NO DISPONIBLE"):
        assert ERROR_CODE.search(message) is None, message


def test_field_read_in_two_pairs_is_one_step():
    frames = [make_frame(i, float(i), "CLIENTES") for i in range(3)]
    typed = {"action": "type", "target": "RAZON SOCIAL", "element": "field"}
    pairs = [
        pair(0, 1, typed | {"value": "FERRETERIA EL CASTANAL R.A.C.I"}),
        pair(1, 2, typed | {"value": "FERRETERIA EL CASTANAL S.A.C."}),
    ]
    steps = build_steps(frames, pairs)
    assert [s.action for s in steps] == ["open_screen", "type"]
    assert steps[1].value == "FERRETERIA EL CASTANAL S.A.C."
    assert [r["value"] for r in steps[1].readings] == ["FERRETERIA EL CASTANAL R.A.C.I", "FERRETERIA EL CASTANAL S.A.C."]


def test_a_value_read_while_still_typed_is_the_same_step():
    frames = [make_frame(i, float(i), "CLIENTES") for i in range(4)]
    typed = {"action": "type", "element": "field"}
    pairs = [
        pair(0, 1, typed | {"target": "RAZON SOCIAL", "value": "FERRETERI"}),
        pair(1, 2, typed | {"target": "RAZON SOCIAL", "value": "FERRETERIA EL CASTANAL S.A.C."},
             typed | {"target": "DIRECCION", "value": "Jl"}),
        pair(2, 3, typed | {"target": "DIRECCION", "value": "JR. LORETO 455"}),
    ]
    steps = build_steps(frames, pairs)
    assert [(s.target, s.value) for s in steps[1:]] == [
        ("RAZON SOCIAL", "FERRETERIA EL CASTANAL S.A.C."), ("DIRECCION", "JR. LORETO 455")]
    assert [r["value"] for r in steps[1].readings] == ["FERRETERIA EL CASTANAL S.A.C."]  # no half typed reading


def test_new_value_after_a_click_is_a_new_step():
    frames = [make_frame(i, float(i), "FACTURAS") for i in range(3)]
    pick = {"action": "select", "target": "CLIENTE", "element": "list"}
    pairs = [
        pair(0, 1, pick | {"value": "A S.A.C."}, {"action": "click", "target": "BUSCAR", "element": "button"}),
        pair(1, 2, pick | {"value": "A S.A.C."}),
    ]
    assert [s.action for s in build_steps(frames, pairs)] == ["open_screen", "select", "click", "select"]


def checked(frames, pairs):
    steps = build_steps(frames, pairs)
    cross_check(steps, frames, pairs)
    return {s.target: s for s in steps}


TYPE_RUC = {"action": "type", "target": "RUC", "element": "field"}


def test_value_seen_again_is_high():
    frames = [make_frame(0, 0.0, "CLIENTES", {"RUC": ""})] + [
        make_frame(i, float(i), "CLIENTES", {"RUC": "20612345678"}) for i in (1, 2)
    ]
    pairs = [pair(0, 1, TYPE_RUC | {"value": "20612345678"}), pair(1, 2)]
    step = checked(frames, pairs)["RUC"]
    assert (step.value, step.confidence, step.variants) == ("20612345678", "high", [])


def test_single_reading_is_medium():
    frames = [make_frame(0, 0.0, "CLIENTES"), make_frame(1, 1.0, "CLIENTES")]
    step = checked(frames, [pair(0, 1, TYPE_RUC | {"value": "123"})])["RUC"]
    assert step.confidence == "medium"


def test_clear_majority_wins_and_keeps_the_variant():
    pick = {"action": "select", "target": "CLIENTE", "element": "list", "value": "TAMBOPATA S.A.C."}
    frames = [make_frame(0, 0.0, "FACTURAS", {"CLIENTE": "(TODOS)"})]
    frames += [make_frame(i, float(i), "FACTURAS", {"CLIENTE": "TAMBOPATA S.A.C."}) for i in (1, 2)]
    frames += [make_frame(3, 3.0, "PAGO", {"CLIENTE": "TAMBOPATAS S.A.C."})]
    pairs = [pair(0, 1, pick), pair(1, 2), pair(2, 3)]
    step = checked(frames, pairs)["CLIENTE"]
    assert (step.value, step.confidence, step.variants) == ("TAMBOPATA S.A.C.", "medium", ["TAMBOPATAS S.A.C."])


def test_disagreement_without_majority_is_low():
    frames = [make_frame(0, 0.0, "CLIENTES", {"RAZON SOCIAL": ""}),
              make_frame(1, 1.0, "CLIENTES", {"RAZON SOCIAL": "CASTANAL R.A.C.I"}),
              make_frame(2, 2.0, "CLIENTES", {"RAZON SOCIAL": "CASTANAL S.A.C."})]
    typed = {"action": "type", "target": "RAZON SOCIAL", "element": "field"}
    pairs = [pair(0, 1, typed | {"value": "CASTANAL R.A.C.I"}), pair(1, 2, typed | {"value": "CASTANAL S.A.C."})]
    step = checked(frames, pairs)["RAZON SOCIAL"]
    # 2 against 2: the latest reading wins, but it is flagged.
    assert (step.value, step.confidence, step.variants) == ("CASTANAL S.A.C.", "low", ["CASTANAL R.A.C.I"])


def test_cleared_field_stops_the_readings():
    frames = [make_frame(0, 0.0, "CLIENTES", {"RUC": ""}),
              make_frame(1, 1.0, "CLIENTES", {"RUC": "123"}),
              make_frame(2, 2.0, "CLIENTES", {"RUC": ""}),
              make_frame(3, 3.0, "CLIENTES", {"RUC": "999"})]
    pairs = [pair(0, 1, TYPE_RUC | {"value": "123"}), pair(1, 2), pair(2, 3)]
    step = checked(frames, pairs)["RUC"]
    assert [r["source"] for r in step.readings] == ["pair 0->1", "frame 1"]


def test_hidden_value_recovered_from_status_bar():
    frames = [make_frame(0, 0.0, "LOGIN"), make_frame(1, 1.0, "MENU", status=["USUARIO: ADMIN", "26/10/2026"])]
    typed = {"action": "type", "target": "USUARIO", "element": "field", "value_visible": False}
    step = checked(frames, [pair(0, 1, typed)])["USUARIO"]
    assert (step.value, step.confidence) == ("ADMIN", "medium")
    assert step.readings[0]["source"] == "status bar frame 1"


def test_observation_confirms_a_value():
    frames = [make_frame(0, 0.0, "CLIENTES"), make_frame(1, 1.0, "CLIENTES")]
    p = pair(0, 1, TYPE_RUC | {"value": "20612345678"})
    p["analysis"].observations = [Observation(subject="row C0007", before="", after="C0007 | 20612345678 | FERRETERIA")]
    step = checked(frames, [p])["RUC"]
    assert step.confidence == "high"


def test_password_is_masked():
    login = make_frame(0, 0.0, "LOGIN")
    login["analysis"].sections[0].fields = [UIField(label="CLAVE", type="password", editable=True)]
    typed = {"action": "type", "target": "CLAVE", "element": "field", "value_visible": False}
    step = checked([login, make_frame(1, 1.0, "MENU")], [pair(0, 1, typed)])["CLAVE"]
    assert (step.value, step.confidence) == (None, "masked")


def three_steps():
    frames = [make_frame(0, 0.0, "LOGIN"), make_frame(1, 1.0, "MENU")]
    return build_steps(frames, [pair(0, 1, {"action": "click", "target": "ACEPTAR", "element": "button"})])


def synthesis(workflows, rules=()):
    return Synthesis.model_validate({"workflows": workflows, "rules": list(rules)})


def test_synthesis_check_accepts_a_clean_answer():
    answer = synthesis([{"name": "Log in", "goal": "enter", "steps": [1, 2, 3]}],
                       [{"kind": "navigation", "screen": "LOGIN", "description": "ACEPTAR opens MENU", "evidence_steps": [2, 3]}])
    assert check_synthesis(three_steps())(answer) is None


def test_synthesis_check_rejects_bad_step_numbers():
    check = check_synthesis(three_steps())
    assert "does not exist" in check(synthesis([{"name": "A", "goal": "", "steps": [1, 2, 3, 9]}]))
    assert "two workflows" in check(synthesis([{"name": "A", "goal": "", "steps": [1, 2]},
                                              {"name": "B", "goal": "", "steps": [2, 3]}]))
    assert "in no workflow" in check(synthesis([{"name": "A", "goal": "", "steps": [1, 2]}]))


def test_synthesis_input_lists_steps_and_changes():
    frames = [make_frame(0, 0.0, "PAGO"), make_frame(1, 1.0, "FACTURAS")]
    p = pair(0, 1, {"action": "click", "target": "GRABAR", "element": "button"})
    p["analysis"].observations = [Observation(subject="ESTADO", before="PENDIENTE", after="PARCIAL")]
    screens = [{"name": "FACTURAS", "dialogs": [],
                "buttons": [{"label": "REGISTRAR PAGO", "enabled": True, "always_enabled": False}]}]
    text = synthesis_input(build_steps(frames, [p]), [p], screens)
    assert "2. [PAGO] click GRABAR" in text
    assert "ESTADO: PENDIENTE -> PARCIAL" in text
    assert "buttons sometimes disabled: REGISTRAR PAGO" in text
