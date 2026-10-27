from rewind.agents.flows import Action, PairAnalysis, analyze_pair, build_steps, describe
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


def test_new_value_after_a_click_is_a_new_step():
    frames = [make_frame(i, float(i), "FACTURAS") for i in range(3)]
    pick = {"action": "select", "target": "CLIENTE", "element": "list"}
    pairs = [
        pair(0, 1, pick | {"value": "A S.A.C."}, {"action": "click", "target": "BUSCAR", "element": "button"}),
        pair(1, 2, pick | {"value": "A S.A.C."}),
    ]
    assert [s.action for s in build_steps(frames, pairs)] == ["open_screen", "select", "click", "select"]
