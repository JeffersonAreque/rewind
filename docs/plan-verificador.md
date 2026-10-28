# Agent 4 plan (verifier)

1. **Screenshots:** the replay takes a screenshot of the new app after each step (`evidence/new/step_NN.png`). It runs once per workflow, so one failure doesn't hide the other workflows. The legacy image for each step is its `frame_to` keyframe.
2. **Comparison (Muse Spark vision):** one call per non-absorbed step (26) with both images. It returns `same_data`, `same_message` and `same_outcome` (pass, fail or unclear), plus a list of differences. Style, layout, labels, today's date and the 7 listed changes (such as the merged page) don't count. Answers are validated and cached by image hash.
3. **Verdicts:** a step passes if the replay passed and the comparison says pass. A rule passes if all its evidence steps pass. "Unclear" is shown as a warning, never as a pass.
4. **Output:** `runs/<id>/verification.json` and `VERIFICATION.md`, with a pass/fail table per step and per rule, legacy and new images side by side, and the differences found.
5. **`scripts/eval_verifier.py`:** it verifies the healthy app and 3 broken copies (no balance formula, no required field, no EFECTIVO). It must flag the broken step and rule in each copy and fail with an error if it misses any.

- **Files:** `rewind/agents/verifier.py`, a screenshot hook in `replay.py`, `rewind/__main__.py` (`verify`), `scripts/eval_verifier.py`, `tests/test_verifier.py`, docs.
- **Commands:**
  - `uv run python -m rewind verify runs/<id>`
  - `uv run python scripts/eval_verifier.py`
- **Tests:** without the model, the step and rule logic and the report, using fake answers.
- **Cost:** about 150k tokens for the first verification (26 calls). The broken copies only pay for the screenshots that change (about 100k). Repeating costs 0. About 6 small commits, then the push.
