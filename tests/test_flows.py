from rewind.agents.flows import analyze_pair, describe
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
