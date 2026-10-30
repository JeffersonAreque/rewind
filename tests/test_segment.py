"""SAM 3.1 segmentation without a GPU: matching boxes to Agent 1's elements, and the cache."""

import json

from rewind import segment
from rewind.agents.screens import FrameAnalysis
from rewind.segment import distinct, match, normalized, reading_order

ANALYSIS = FrameAnalysis.model_validate({
    "screen_name": "PACIENTES", "window_title": "PACIENTES",
    "sections": [{"fields": [
        {"label": "DNI", "type": "text", "editable": True}, {"label": "FECHA NAC.", "type": "date", "editable": True},
        {"label": "APELLIDOS", "type": "text", "editable": True},
        {"label": "SEGURO", "type": "radio", "editable": True},  # radios get no box
    ]}],
    "buttons": [{"label": "GRABAR"}, {"label": "NUEVO"}],
})


def found(*boxes, score=0.9):
    return [{"box": list(b), "score": score} for b in boxes]


def test_boxes_become_fractions_of_the_image():
    assert normalized([128, 72, 640, 144], 1280, 720) == [0.1, 0.1, 0.5, 0.2]
    assert normalized([0.1, 0.1, 0.5, 0.2], 1280, 720) == [0.1, 0.1, 0.5, 0.2]


def test_one_box_per_element_and_only_confident_ones():
    boxes = found((0.1, 0.1, 0.3, 0.15), (0.101, 0.1, 0.3, 0.151)) + found((0.5, 0.5, 0.6, 0.6), score=0.2)
    assert distinct(boxes) == [[0.1, 0.1, 0.3, 0.15]]


def test_reading_order_is_rows_then_left_to_right():
    left_low, right_top, left_top = [0.1, 0.3, 0.3, 0.35], [0.6, 0.1, 0.8, 0.15], [0.1, 0.11, 0.3, 0.16]
    assert reading_order([left_low, right_top, left_top]) == [left_top, right_top, left_low]


def test_boxes_match_elements_only_when_the_counts_agree():
    answer = {"width": 1280, "height": 720, "found": {
        "input box": found((640, 72, 900, 100), (128, 72, 400, 100), (128, 144, 400, 172)),
        "button": found((1000, 72, 1100, 100)),  # one button missing: no button boxes at all
        "table": [],
    }}
    boxes, mismatched = match(ANALYSIS, answer)
    assert boxes == {"DNI": [0.1, 0.1, 0.3125, 0.1389], "FECHA NAC": [0.5, 0.1, 0.7031, 0.1389],
                     "APELLIDOS": [0.1, 0.2, 0.3125, 0.2389]}
    assert mismatched == {"button": [2, 1]}


def test_segments_are_cached_by_image(tmp_path, monkeypatch):
    monkeypatch.setattr(segment, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "0.png").write_bytes(b"png")
    (tmp_path / "frames.json").write_text(json.dumps([
        {"index": 0, "time": 0.0, "image": "frames/0.png", "analysis": ANALYSIS.model_dump()}]))
    calls = []

    def fake_sam(images, repo):
        calls.append(len(images))
        return [{"width": 1280, "height": 720, "model": repo,
                 "found": {"button": found((128, 600, 200, 630), (300, 600, 380, 630))}}]

    path = segment.run(tmp_path, "facebook/sam3.1", segment=fake_sam, log=lambda _: None)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "facebook/sam3.1" and set(data["frames"]["0"]["boxes"]) == {"GRABAR", "NUEVO"}
    segment.run(tmp_path, "facebook/sam3.1", segment=fake_sam, log=lambda _: None)
    assert calls == [1]  # the second run used the cache


def test_segmentation_is_off_unless_asked(monkeypatch):
    monkeypatch.setattr(segment, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("REWIND_SEGMENTATION", raising=False)
    assert not segment.enabled()
    monkeypatch.setenv("REWIND_SEGMENTATION", "SAM")
    assert segment.enabled()
