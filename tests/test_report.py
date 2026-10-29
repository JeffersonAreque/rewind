"""The HTML report: what it collects from a run, partial runs and embedded images."""

import json

import pytest

from rewind import report

SCREEN = {"name": "REGISTRO DE CLIENTES", "image": "frames/kf_001.png", "frames": [1],
          "sections": [{"name": "", "fields": [{"label": "RUC / DNI"}]}], "buttons": [{"label": "GRABAR"}]}
STEPS = [
    {"n": 1, "screen": "REGISTRO DE CLIENTES", "action": "open_screen", "target": "REGISTRO DE CLIENTES"},
    {"n": 2, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "GRABAR"},
    {"n": 3, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "ACEPTAR"},
]
VERIFIED = [
    {"n": 1, "screen": "REGISTRO DE CLIENTES", "action": "open_screen", "target": "REGISTRO DE CLIENTES",
     "value": None, "workflow": "Register customer", "frame_to": 1, "absorbed": None,
     "legacy_image": "frames/kf_001.png", "new_image": "evidence/new/step_01.png", "replay": "pass",
     "replay_error": None, "comparison": {"same_data": "pass", "same_message": "pass", "same_outcome": "pass",
                                          "differences": []}, "comparison_error": None, "verdict": "pass"},
    {"n": 2, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "GRABAR", "value": None,
     "workflow": "Register customer", "frame_to": 1, "absorbed": None, "legacy_image": "frames/kf_001.png",
     "new_image": "evidence/new/step_02.png", "replay": "fail", "replay_error": "message '0x0042' is not shown",
     "comparison": None, "comparison_error": None, "verdict": "fail"},
    {"n": 3, "screen": "REGISTRO DE CLIENTES", "action": "click", "target": "ACEPTAR", "value": None,
     "workflow": "Register customer", "frame_to": 2, "absorbed": "inline_validation (change 1)",
     "legacy_image": None, "new_image": None, "replay": "absorbed", "replay_error": None,
     "comparison": None, "comparison_error": None, "verdict": "absorbed"},
]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def run_dir(tmp_path):
    write(tmp_path / "screens.json", {"video": "samples/demo.mp4", "model": "m", "screens": [SCREEN]})
    for name in ("frames/kf_001.png", "evidence/new/step_01.png", "evidence/new/step_02.png"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(name.encode())
    return tmp_path


def verify(run_dir):
    write(run_dir / "flows.json", {"steps": STEPS, "workflows": [
        {"name": "Register customer", "goal": "Create a customer", "steps": [1, 2, 3]}], "rules": [
        {"kind": "required_field", "description": "RUC / DNI is required", "evidence_steps": [2]}]})
    write(run_dir / "app" / "spec.json", {"title": "Factura Ágil", "screens": [
        {"legacy": "REGISTRO DE CLIENTES", "title": "Clientes", "fields": [], "actions": []}], "changes": [
        {"kind": "inline_validation", "screens": ["REGISTRO DE CLIENTES"], "element": None, "legacy": "",
         "modernized": "", "reason": "No dialog to close", "evidence_steps": [2, 3]}]})
    write(run_dir / "verification.json", {
        "summary": {"steps": {"pass": 1, "fail": 1, "unclear": 0, "absorbed": 1},
                    "rules": {"pass": 0, "fail": 1, "unclear": 0}},
        "steps": VERIFIED, "rules": [{"n": 1, "verdict": "fail"}]})


def test_a_run_with_only_screens_still_gets_a_report(run_dir):
    data = report.collect(run_dir)
    assert data["title"] == "demo" and data["screens"][0]["new_image"] is None
    assert data["workflows"] == [] and data["summary"] is None
    html = report.render(data, report.images(data, run_dir))
    assert "REGISTRO DE CLIENTES" in html and "Not verified yet" in html


def test_full_run_links_screens_workflows_rules_and_changes(run_dir):
    verify(run_dir)
    data = report.collect(run_dir)
    screen = data["screens"][0]
    assert screen["modern"] == "Clientes" and screen["new_image"] == "evidence/new/step_01.png"
    assert [i["verdict"] for i in data["workflows"][0]["items"]] == ["pass", "fail", "absorbed"]
    assert data["rules"][0]["verdict"] == "fail"
    assert data["changes"][0]["after"] == "Error shown under the field, nothing to close"

    html = report.render(data, report.images(data, run_dir))
    assert "<h1>Factura Ágil</h1>" in html
    assert "message &#39;0x0042&#39; is not shown" in html  # escaped
    assert "<details open>" in html and "Not needed in the new app: inline_validation (change 1)." in html


def test_each_image_is_embedded_once(run_dir):
    verify(run_dir)
    data = report.collect(run_dir)
    embedded = report.images(data, run_dir)
    assert sorted(embedded) == ["evidence/new/step_01.png", "evidence/new/step_02.png", "frames/kf_001.png"]
    html = report.render(data, embedded)
    assert html.count(embedded["frames/kf_001.png"]) == 1
    assert html.count('data-img="frames/kf_001.png"') == 3  # before/after and two steps


def test_embedded_data_cannot_close_the_script(run_dir):
    html = report.render(report.collect(run_dir), {"x": "</script><b>"})
    assert "</script><b>" not in html and "<\\/script>" in html


def test_a_folder_without_screens_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="no screens.json"):
        report.collect(tmp_path)
