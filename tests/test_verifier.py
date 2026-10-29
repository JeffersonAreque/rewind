"""Verifier pieces that need no model: replay results, verdicts, the report and the cached comparison."""

import json

from rewind.agents import verifier
from rewind.agents.verifier import (Comparison, check_comparison, compare, replay_results, report_md,
                                    rule_verdict, step_evidence, step_verdict)
from tests.fakes import FakeClient


def step(n, action, target, frame_to, **extra):
    return {"n": n, "screen": "REGISTRO DE CLIENTES", "action": action, "target": target,
            "frame_from": frame_to - 1, "frame_to": frame_to} | extra


STEPS = [
    step(1, "type", "RUC / DNI", 1, value="20612345678"),
    step(2, "click", "GRABAR", 1),
    step(3, "see_message", "ERROR 0x0042: CAMPO OBLIGATORIO\nRUC / DNI", 2, code="0x0042"),
    step(4, "click", "ACEPTAR", 3, absorbed="inline_validation (change 1)"),
    step(5, "click", "FACTURAS", 4),
]
WORKFLOWS = [{"name": "Register customer", "steps": [1, 2, 3, 4]}, {"name": "Search invoices", "steps": [5]}]
PASS = {"same_data": "pass", "same_message": "pass", "same_outcome": "pass", "differences": []}


def test_one_fail_fails_the_comparison_and_unclear_is_never_a_pass():
    assert Comparison(**PASS).verdict == "pass"
    assert Comparison(**PASS | {"same_message": "unclear", "differences": ["x"]}).verdict == "unclear"
    assert Comparison(**PASS | {"same_message": "unclear", "same_data": "fail", "differences": ["x"]}).verdict == "fail"
    assert check_comparison(Comparison(**PASS | {"same_data": "fail"})) is not None


def test_replay_results_read_the_files_the_replay_left(tmp_path):
    (tmp_path / "step_01.json").write_text(json.dumps({"n": 1, "error": None}))
    (tmp_path / "step_02.json").write_text(json.dumps({"n": 2, "error": "message 0x0042 is not shown"}))
    results = replay_results(tmp_path, STEPS, WORKFLOWS)
    assert results[1] == {"replay": "pass", "error": None}
    assert results[2]["replay"] == "fail"
    assert results[3] == {"replay": "not reached", "error": "step 2 of this workflow failed first"}
    assert results[4]["replay"] == "absorbed"
    assert results[5] == {"replay": "not reached", "error": "the replay did not reach it"}


def test_a_step_passes_only_when_it_replayed_and_the_comparison_passed():
    unclear = Comparison(**PASS | {"same_data": "unclear", "differences": ["x"]})
    assert step_verdict("pass", Comparison(**PASS)) == "pass"
    assert step_verdict("pass", unclear) == "unclear"
    assert step_verdict("pass", None) == "unclear"  # the model gave no valid answer
    assert step_verdict("fail", Comparison(**PASS)) == "fail"
    assert step_verdict("not reached", None) == "fail"
    assert step_verdict("absorbed", None) == "absorbed"


def test_a_rule_passes_only_when_all_its_evidence_steps_pass():
    verdicts = {1: "pass", 2: "pass", 3: "unclear", 4: "absorbed", 5: "fail"}
    assert rule_verdict([1, 2, 4], verdicts) == "pass"
    assert rule_verdict([1, 3], verdicts) == "unclear"
    assert rule_verdict([3, 5], verdicts) == "fail"
    assert rule_verdict([4], verdicts) == "unclear"  # nothing left to check
    assert rule_verdict([99], verdicts) == "unclear"


def test_evidence_names_the_later_steps_inside_the_same_keyframe():
    text = step_evidence(STEPS[0], STEPS, ["inline_validation: dialog -> under the field"])
    assert "THIS STEP\n1. [REGISTRO DE CLIENTES] type RUC / DNI = '20612345678'" in text
    assert "LATER STEPS" in text and "2. [REGISTRO DE CLIENTES] click GRABAR" in text
    assert "3." not in text.split("LATER STEPS")[1].split("MODERNIZATION")[0]
    assert "1. inline_validation: dialog -> under the field" in text
    assert "LATER STEPS" not in step_evidence(STEPS[2], STEPS, [])
    password = step(6, "type", "CLAVE", 5, confidence="masked")
    assert "own test password" in step_evidence(password, STEPS, []) and "own test" not in text
    assert "narrower than its value" in text  # STEPS[0] types a value
    assert "narrower" not in step_evidence(STEPS[1], STEPS, [])


def test_comparisons_are_cached_by_image(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "CACHE_DIR", tmp_path / "cache")
    legacy, new, other = tmp_path / "legacy.png", tmp_path / "new.png", tmp_path / "other.png"
    legacy.write_bytes(b"legacy")
    new.write_bytes(b"new")
    other.write_bytes(b"other")
    fail = PASS | {"same_data": "fail", "differences": ["SALDO is S/ 640.00, not S/ 340.00"]}
    client = FakeClient(json.dumps(fail | {"differences": []}), json.dumps(fail), json.dumps(PASS))

    first = compare(client, "m", STEPS[0], STEPS, [], legacy, new)
    assert first["analysis"].verdict == "fail" and not first["cached"]
    assert len(client.calls) == 2 and len(client.calls[1]) == 3  # the empty differences were sent back
    assert compare(client, "m", STEPS[0], STEPS, [], legacy, new)["cached"]
    assert compare(client, "m", STEPS[0], STEPS, [], legacy, other)["analysis"].verdict == "pass"


def result_step(n, verdict, replay="pass", comparison=None, **extra):
    return {"n": n, "screen": "REGISTRAR PAGO", "action": "click", "target": "GRABAR", "value": None, "code": None,
            "workflow": "Register payment", "frame_to": 15, "absorbed": None,
            "legacy_image": "frames/kf_015.png", "new_image": f"evidence/new/step_{n:02d}.png",
            "replay": replay, "replay_error": None, "comparison": comparison, "comparison_error": None,
            "verdict": verdict} | extra


def test_report_shows_both_images_the_differences_and_the_rule_verdicts():
    diff = PASS | {"same_data": "fail", "differences": ["SALDO is S/ 640.00, not S/ 340.00"]}
    result = {
        "title": "Factura Ágil",
        "summary": {"steps": {"pass": 1, "fail": 2, "unclear": 0, "absorbed": 1},
                    "rules": {"pass": 0, "fail": 1, "unclear": 0}},
        "steps": [
            result_step(26, "pass", comparison=PASS),
            result_step(27, "fail", comparison=diff),
            result_step(28, "fail", replay="fail", new_image="evidence/new/step_28.png",
                        replay_error="step 28: page does not show ['S/ 340.00']"),
            result_step(29, "absorbed", replay="absorbed", absorbed="remove_dialog_step (change 4)",
                        legacy_image=None, new_image=None),
        ],
        "rules": [{"n": 1, "description": "SALDO goes from 640.00 to 340.00", "evidence_steps": [26, 27, 28],
                   "verdict": "fail"}],
    }
    md = report_md(result)
    assert "**Steps:** 1 pass, 2 fail, 0 unclear, 1 absorbed by changes" in md
    assert "| 1 | SALDO goes from 640.00 to 340.00 | 26, 27, 28 | **FAIL** |" in md
    assert "| 27 | Register payment | [REGISTRAR PAGO] click GRABAR | pass | fail | pass | pass | **FAIL** |" in md
    assert "| ![step 27 in the recording](frames/kf_015.png) | ![step 27 in the new app](evidence/new/step_27.png) |" in md
    assert "- SALDO is S/ 640.00, not S/ 340.00" in md
    assert "- Replay: step 28: page does not show ['S/ 340.00']" in md
    assert "### Step 29" not in md and "- Step 29 (`29. [REGISTRAR PAGO] click GRABAR`): remove_dialog_step (change 4)" in md
