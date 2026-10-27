"""Replays the steps of the legacy recording on the new app (Playwright).

Same file in every generated app. The steps come from steps.json, which
Rewind writes from flows.json: every element is found by its legacy label
(data-legacy) so the test speaks the language of the original recording.
"""

import json
import re
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 5000  # ms to wait for an element, a message or a value

# Same normalization as Rewind: capitals, no accents, no colons, single spaces, no trailing dot.
NORMALIZE = r"""s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toUpperCase()
  .replace(/:/g, ' ').replace(/\s+/g, ' ').trim().replace(/[ .]+$/, '')"""

FIND = f"""([attr, label]) => {{
  const n = {NORMALIZE};
  return [...document.querySelectorAll(`[${{attr}}]`)]
    .find(e => n(e.getAttribute(attr)) === n(label) && e.getClientRects().length > 0) || null;
}}"""

PAGE_HAS = f"""(texts) => {{
  const n = {NORMALIZE};
  const page = n(document.body.innerText);
  return texts.every(t => page.includes(n(t)));
}}"""

ROW_COUNT = f"""([label, count]) => {{
  const n = {NORMALIZE};
  const table = [...document.querySelectorAll('table[data-legacy]')].find(t => n(t.dataset.legacy) === n(label));
  return table && table.querySelectorAll('tbody tr[data-id]').length === count;
}}"""


def load_steps() -> list[dict]:
    return json.loads((HERE / "steps.json").read_text(encoding="utf-8"))


class StepFailed(AssertionError):
    pass


def find(page, label: str, attr: str = "data-legacy"):
    try:
        return page.wait_for_function(FIND, arg=[attr, label], timeout=TIMEOUT).as_element()
    except Exception:
        raise StepFailed(f"no visible element with {attr}={label!r}") from None


def wait_for(page, script: str, arg, what: str) -> None:
    try:
        page.wait_for_function(script, arg=arg, timeout=TIMEOUT)
    except Exception:
        raise StepFailed(what) from None


def to_iso(value: str) -> str:
    if m := re.match(r"^(\d{2})/(\d{2})/(\d{4})$", value):
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    return value


def do(page, step: dict, password: str) -> None:
    action, target, value = step["action"], step["target"], step.get("value")
    if action == "open_screen":
        find(page, target, "data-legacy-screen")
    elif action == "type":
        element = find(page, target)
        text = password if step.get("confidence") == "masked" else value
        if element.get_attribute("type") == "date":
            text = to_iso(text)
        element.fill(text or "")
    elif action == "select":
        element = find(page, target)
        tag = element.evaluate("e => e.tagName")
        if tag == "SELECT":
            option = element.evaluate(
                f"(e, v) => {{ const n = {NORMALIZE}; const o = [...e.options]"
                f".find(o => n(o.text) === n(v) || o.value === v); return o ? o.value : null; }}", value)
            if option is None:
                raise StepFailed(f"{target!r} has no option {value!r}")
            element.select_option(option)
        elif tag == "TABLE":
            row = element.evaluate_handle(
                f"(t, v) => {{ const n = {NORMALIZE}; return [...t.querySelectorAll('tbody tr')]"
                f".find(r => n(r.innerText).includes(n(v))) || null; }}", value).as_element()
            if row is None:
                raise StepFailed(f"table {target!r} has no row with {value!r}")
            row.click()
        else:  # a group of radio buttons
            label = element.evaluate_handle(
                f"(g, v) => {{ const n = {NORMALIZE}; return [...g.querySelectorAll('label')]"
                f".find(l => n(l.innerText) === n(v)) || null; }}", value).as_element()
            if label is None:
                raise StepFailed(f"{target!r} has no choice {value!r}")
            label.click()
    elif action == "click":
        find(page, target).click()
        page.wait_for_load_state()
    elif action == "see_message":
        expected = step.get("code") or target
        wait_for(page, PAGE_HAS, [expected], f"message {expected!r} is not shown")


def check(page, c: dict) -> None:
    if c["kind"] == "text":
        wait_for(page, PAGE_HAS, c["texts"], f"page does not show {c['texts']}")
    elif c["kind"] == "row_count":
        wait_for(page, ROW_COUNT, [c["table"], c["count"]], f"table {c['table']!r} does not have {c['count']} rows")
    elif c["kind"] == "button":
        disabled = find(page, c["button"]).is_disabled()
        if disabled == c["enabled"]:
            raise StepFailed(f"button {c['button']!r} should be {'enabled' if c['enabled'] else 'disabled'}")


def replay(page, app: dict, steps: list[dict], first: int, last: int) -> dict:
    """Replay steps 1..last; steps first..last are this workflow's and are checked.

    Earlier steps only bring the app to where the workflow starts (for example,
    paying an invoice needs the login and the search). Returns what was done.
    """
    page.goto(app["url"] + "/login")
    done = {"replayed": [], "absorbed": []}
    for step in steps:
        if step["n"] > last:
            break
        mine = step["n"] >= first
        where = f"step {step['n']} ({step['action']} {step['target']!r} on {step['screen']})"
        try:
            for c in step.get("before", []) if mine else []:
                check(page, c)
            if step.get("absorbed"):
                done["absorbed"].append(step["n"])
                continue
            do(page, step, app["password"])
            for c in step.get("after", []) if mine else []:
                check(page, c)
        except StepFailed as exc:
            context = "" if mine else f"while preparing (this workflow starts at step {first}), "
            raise StepFailed(f"{context}{where}: {exc}") from None
        done["replayed"].append(step["n"])
    return done
