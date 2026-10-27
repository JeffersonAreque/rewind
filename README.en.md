# Rewind

[Español](README.md) · **English**

> Built from scratch during the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).

Modernize legacy software from a screen recording, with no source code. Record someone using the old program; a team of agents rebuilds its screens and workflows as a modern web app, then checks step by step that it behaves the same.

![Sample billing system with a Windows 98 look](docs/img/legacy-demo.png)

## How it works

1. **Screens agent**: pulls keyframes, screens, fields, buttons, tables and dialogs out of the video.
2. **Flows agent**: rebuilds the workflows step by step.
3. **Builder agent**: generates a web app with the same screens and workflows.
4. **Verifier agent**: replays every workflow in the new app and compares it against the original video.

**Muse Spark Open** (Meta's open-weights model, served by Baseten) is the core and does all the work. **SAM 3.1** is an optional add-on that will only be turned on if Meta's API becomes available later; nothing depends on it.

More detail in [docs/arquitectura.md](docs/arquitectura.md) (Spanish).

## Status

| Part | Status |
|---|---|
| Sample legacy app (`legacy-demo/`) | Done |
| Provider config (Baseten / Fireworks) | Done |
| Agent 1: screens | Done: 100% accuracy on the sample video |
| Agent 2: flows | Done: 29/29 steps, 11/11 values, 4/4 workflows and 7/7 rules on the sample video |
| Agents 3 and 4 | In progress |

## Stack

Python 3.12 · uv · Muse Spark Open (via Baseten or Fireworks, OpenAI-compatible API) · ffmpeg · numpy · pydantic · Flask · SQLite · 98.css

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv downloads Python 3.12 on its own if you don't have it.

```
git clone https://github.com/JeffersonAreque/rewind.git
cd rewind
uv sync
cp .env.example .env    # then add your API key and model
```

## Usage

Sample legacy app (user `admin`, password `admin`):

```
uv run python legacy-demo/app.py --reset
```

Open http://127.0.0.1:5000. To record it as input, follow [docs/grabar-video.md](docs/grabar-video.md) (Spanish).

Show the active model provider:

```
uv run python -m rewind config
```

To switch providers, set `REWIND_PROVIDER` in `.env` to `fireworks` or `baseten`.

Test the provider with one text call and one vision call (a frame of the sample video). It prints the answer and token counts, never the key; `--reasoning` also prints the model's reasoning:

```
uv run python scripts/check_provider.py
```

Agent 1, screens (needs ffmpeg). Reads the video and writes `runs/<id>/screens.json` plus the keyframes; `--dry-run` only extracts the keyframes and spends no credits:

```
uv run python -m rewind screens samples/facturacion-demo.mp4
uv run python scripts/eval_screens.py     # accuracy against samples/facturacion-demo.expected.json
```

Agent 2, flows. Reads that run and writes `runs/<id>/flows.json`: the user's steps, the business workflows and the business rules. Misread values are flagged as low confidence instead of trusted:

```
uv run python -m rewind flows runs/<id>
uv run python scripts/eval_flows.py       # % of steps, values, workflows and rules
```

Tests that make no model calls:

```
uv run pytest
```

## Layout

```
legacy-demo/   sample legacy app (Flask + SQLite + 98.css)
rewind/        Python agent pipeline
docs/          architecture and recording guide
samples/       input video and expected answers
scripts/       video recording, provider smoke test and accuracy check
tests/         tests that need no API
```

## Author

Jefferson Areque, solo entry (Indie track).

## License

[MIT](LICENSE). 98.css (MIT) and the MS Sans Serif font (CC BY-SA 3.0) keep their own licenses; see [legacy-demo/README.md](legacy-demo/README.md#créditos).
