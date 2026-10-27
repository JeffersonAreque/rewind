import pytest
from pydantic import ValidationError

from rewind.agents.screens import (
    Dialog,
    FrameAnalysis,
    Section,
    UIField,
    analyze_cached,
    analyze_frame,
    cache_key,
    extract_json,
    group_screens,
    merge_fields,
    normalize,
)
from rewind.llm import TokenCount
from tests.fakes import FakeClient

VALID = '{"screen_name": "LOGIN", "window_title": "INICIO DE SESION"}'


@pytest.mark.parametrize("answer", [
    VALID,
    f"```json\n{VALID}\n```",
    f"Here it is:\n{VALID}\nDone.",
])
def test_extract_json(answer):
    assert FrameAnalysis.model_validate_json(extract_json(answer)).screen_name == "LOGIN"


def test_retry_sends_validation_error_back(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient('{"screen_name": "LOGIN"}', VALID)
    tokens = TokenCount()

    analysis, reasoning = analyze_frame(client, "model", image, tokens)

    assert analysis.window_title == "INICIO DE SESION"
    assert reasoning == "thinking"
    assert tokens.calls == 2
    assert "window_title" in client.calls[1][-1]["content"]


def test_cache_avoids_second_call(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient(VALID)

    first = analyze_cached(client, "model", image, tmp_path / "cache")
    second = analyze_cached(client, "model", image, tmp_path / "cache")

    assert not first["cached"] and second["cached"]
    assert second["analysis"] == first["analysis"]
    assert len(client.calls) == 1


def test_cache_key_depends_on_model_and_image(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert cache_key(a, "m1") != cache_key(b, "m1")
    assert cache_key(a, "m1") != cache_key(a, "m2")


def test_gives_up_after_retries(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient("no json", "still no json", "nope")
    with pytest.raises(ValidationError):
        analyze_frame(client, "model", image, TokenCount())


def test_normalize():
    assert normalize("  Razón social: ") == "RAZON SOCIAL"
    assert normalize("REGISTRO  DE clientes") == "REGISTRO DE CLIENTES"
    assert normalize("MONTO S/.") == normalize("MONTO S/") == "MONTO S/"
    assert normalize("NRO. FACTURA") == "NRO. FACTURA"


def test_merge_fields_majority_and_values():
    seen = [
        (1.0, UIField(label="NRO. FACTURA", editable=True)),
        (2.0, UIField(label="NRO. FACTURA", editable=False, value="F001")),
        (3.0, UIField(label="NRO. FACTURA", editable=True, value="F001")),
    ]
    merged = merge_fields(seen)
    assert merged["editable"] is True
    assert merged["values"] == [{"time": 2.0, "value": "F001"}]
    assert merged["seen"] == 3


def frame(index, time, screen, fields=(), dialog=None):
    analysis = FrameAnalysis(
        screen_name=screen,
        window_title=f"APP - [{screen}]",
        sections=[Section(name="DATOS", fields=[UIField(label=f, editable=True) for f in fields])],
        dialog=dialog,
    )
    return {"index": index, "time": time, "image": f"frames/{index}.png", "analysis": analysis}


def test_group_screens_and_navigation():
    error = Dialog(kind="error", title="ERROR", message="ERROR 0x0042: CAMPO OBLIGATORIO")
    frames = [
        frame(0, 0.5, "INICIO DE SESION", ["USUARIO", "CLAVE"]),
        frame(1, 6.0, "REGISTRO DE CLIENTES", ["RUC / DNI"]),
        frame(2, 9.0, "Registro de clientes", ["RUC / DNI"], dialog=error),
        frame(3, 12.0, "REGISTRO DE CLIENTES", ["RUC / DNI", "RAZON SOCIAL"]),
        frame(4, 20.0, "BUSQUEDA DE FACTURAS"),
        frame(5, 30.0, "REGISTRO DE CLIENTES"),
    ]
    screens, navigation = group_screens(frames)

    assert [s["id"] for s in screens] == ["inicio-de-sesion", "registro-de-clientes", "busqueda-de-facturas"]
    clientes = screens[1]
    assert clientes["frames"] == [1, 2, 3, 5]
    assert clientes["image"] == "frames/3.png"  # no dialog, most fields
    assert [d["message"] for d in clientes["dialogs"]] == ["ERROR 0x0042: CAMPO OBLIGATORIO"]
    assert [f["label"] for f in clientes["sections"][0]["fields"]] == ["RUC / DNI", "RAZON SOCIAL"]
    assert [n["screen"] for n in navigation] == [
        "inicio-de-sesion", "registro-de-clientes", "busqueda-de-facturas", "registro-de-clientes",
    ]


def test_table_keeps_most_rows_seen():
    from rewind.agents.screens import Table

    empty, found = frame(0, 1.0, "BUSQUEDA"), frame(1, 2.0, "BUSQUEDA")
    empty["analysis"].tables = [Table(name="RESULTADOS", columns=["NRO", "TOTAL"], rows=0)]
    found["analysis"].tables = [Table(name="RESULTADOS", columns=["NRO", "TOTAL"], rows=3)]
    screens, _ = group_screens([empty, found])
    assert screens[0]["tables"][0]["rows"] == 3
