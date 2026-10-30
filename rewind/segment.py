"""Optional segmentation of the legacy keyframes with SAM 3.1 (on a Modal GPU).

Off unless REWIND_SEGMENTATION=sam in .env. SAM finds the boxes of the input
boxes, buttons and tables of each keyframe; each box is given to the element
Agent 1 read, in reading order, and only when SAM found exactly as many boxes
as Agent 1 read elements of that kind. Otherwise that kind gets no boxes:
nothing is guessed.

The result goes to runs/<id>/segments.json, apart from screens.json, so no
Muse Spark prompt changes and no cached call is lost. The report uses it to
outline the element each verified step acted on. Answers are cached by image
in runs/cache/sam/, so repeating costs no GPU time.
"""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from rewind.agents.screens import FrameAnalysis, normalize
from rewind.config import ROOT

CACHE_DIR = ROOT / "runs" / "cache" / "sam"
MIN_SCORE = 0.5
SAME_BOX = 0.7  # IoU above which two boxes are the same element found twice

# Agent 1 kind -> what SAM is asked to find. Radio buttons have no box of their own.
CONCEPTS = {"input": "input box", "button": "button", "table": "table"}
INPUT_TYPES = ("text", "password", "select", "date", "amount", "other")


def enabled() -> bool:
    load_dotenv(ROOT / ".env")
    return os.getenv("REWIND_SEGMENTATION", "").strip().lower() == "sam"


Box = list[float]  # x0, y0, x1, y1 as fractions of the image (0..1)


def normalized(box: list[float], width: int, height: int) -> Box:
    """Fractions of the image, whether SAM answered in pixels or already normalized."""
    if max(box) <= 1.0:
        return [round(v, 4) for v in box]
    return [round(box[0] / width, 4), round(box[1] / height, 4), round(box[2] / width, 4), round(box[3] / height, 4)]


def iou(a: Box, b: Box) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def distinct(found: list[dict]) -> list[Box]:
    """Confident boxes, each element once (the best scored of boxes that overlap)."""
    kept: list[Box] = []
    for item in sorted(found, key=lambda f: -f["score"]):
        if item["score"] >= MIN_SCORE and all(iou(item["box"], k) < SAME_BOX for k in kept):
            kept.append(item["box"])
    return kept


def reading_order(boxes: list[Box]) -> list[Box]:
    """Top to bottom in rows (boxes whose middles share a line), left to right in each row."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        middle = (box[1] + box[3]) / 2
        row = rows[-1] if rows else None
        if row and abs(middle - (row[0][1] + row[0][3]) / 2) < (row[0][3] - row[0][1]) / 2:
            row.append(box)
        else:
            rows.append([box])
    return [b for row in rows for b in sorted(row, key=lambda b: b[0])]


def elements(analysis: FrameAnalysis) -> dict[str, list[str]]:
    """Agent 1's labels per kind, in the order it read them."""
    return {
        "input": [f.label for s in analysis.sections for f in s.fields if f.type in INPUT_TYPES],
        "button": [b.label for b in analysis.buttons],
        "table": [t.name for t in analysis.tables],
    }


def match(analysis: FrameAnalysis, answer: dict) -> tuple[dict[str, Box], dict[str, list[int]]]:
    """({normalized label: box}, {kind: [labels, boxes]} for the kinds whose counts differ)."""
    boxes, mismatched = {}, {}
    for kind, labels in elements(analysis).items():
        found = [dict(f, box=normalized(f["box"], answer["width"], answer["height"]))
                 for f in answer["found"].get(CONCEPTS[kind], [])]
        ordered = reading_order(distinct(found))
        if len(ordered) != len(labels):
            if labels or ordered:
                mismatched[kind] = [len(labels), len(ordered)]
            continue
        boxes |= {normalize(label): box for label, box in zip(labels, ordered)}
    return boxes, mismatched


def cache_key(png: bytes, repo: str) -> str:
    parts = [png, repo.encode(), json.dumps(CONCEPTS, sort_keys=True).encode()]
    return hashlib.sha256(b"".join(hashlib.sha256(p).digest() for p in parts)).hexdigest()


def modal_boxes(requests: list[bytes], repo: str) -> list[dict]:
    """Ask SAM on Modal for every image at once (one GPU container, loaded once)."""
    from rewind import sam_modal  # needs the modal package and a Modal token

    concepts = list(CONCEPTS.values())
    with sam_modal.app.run():
        segmenter = sam_modal.Segmenter(repo=repo)
        return list(segmenter.boxes.starmap([(png, concepts) for png in requests]))


def run(run_dir: Path, repo: str | None = None, segment: Callable[[list[bytes], str], list[dict]] = modal_boxes,
        log: Callable[[str], None] = print) -> Path:
    """Write runs/<id>/segments.json for the keyframes of a screens run."""
    repo = repo or os.getenv("SAM_REPO", "facebook/sam3.1")
    frames = [f for f in json.loads((run_dir / "frames.json").read_text(encoding="utf-8")) if "analysis" in f]
    images = {f["index"]: (run_dir / f["image"]).read_bytes() for f in frames}
    keys = {i: cache_key(png, repo) for i, png in images.items()}
    answers = {i: json.loads((CACHE_DIR / f"{k}.json").read_text(encoding="utf-8"))
               for i, k in keys.items() if (CACHE_DIR / f"{k}.json").exists()}
    todo = [i for i in images if i not in answers]
    log(f"SAM: {len(images)} keyframes, {len(answers)} from cache")
    if todo:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for i, answer in zip(todo, segment([images[i] for i in todo], repo)):
            (CACHE_DIR / f"{keys[i]}.json").write_text(json.dumps(answer), encoding="utf-8")
            answers[i] = answer

    result = {"model": None, "frames": {}}
    for f in frames:
        answer = answers[f["index"]]
        boxes, mismatched = match(FrameAnalysis.model_validate(f["analysis"]), answer)
        result["model"] = answer.get("model", repo)
        result["frames"][str(f["index"])] = {"boxes": boxes, "mismatched": mismatched}
    path = run_dir / "segments.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    matched = sum(len(fr["boxes"]) for fr in result["frames"].values())
    log(f"SAM ({result['model']}): {matched} elements outlined; {path}")
    return path
