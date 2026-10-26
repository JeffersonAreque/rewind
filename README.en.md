# Rewind

[Español](README.md) · **English**

> Built from scratch during the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).

Modernize legacy software from a screen recording, with no source code. Record someone using the old program; a team of agents rebuilds its screens and workflows as a modern web app, then checks step by step that it behaves the same.

![Sample billing system with a Windows 98 look](docs/img/legacy-demo.png)

## How it works

1. **Screens agent**: pulls keyframes, screens, fields and buttons out of the video (SAM 3.1 + Muse Spark).
2. **Flows agent**: rebuilds the workflows step by step (Muse Spark).
3. **Builder agent**: generates a web app with the same screens and workflows.
4. **Verifier agent**: replays every workflow in the new app and compares it against the original video.

More detail in [docs/arquitectura.md](docs/arquitectura.md) (Spanish).

## Status

| Part | Status |
|---|---|
| Sample legacy app (`legacy-demo/`) | Done |
| Provider config (Fireworks / Baseten) | Done |
| Agents 1 to 4 | In progress |

## Stack

Python 3.12 · uv · Flask · SQLite · 98.css · Muse Spark (via Fireworks or Baseten, OpenAI-compatible API)

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

## Layout

```
legacy-demo/   sample legacy app (Flask + SQLite + 98.css)
rewind/        Python agent pipeline
docs/          architecture and recording guide
samples/       input videos
scripts/       automated demo video recording (Playwright)
```

## Author

Jefferson Areque, solo entry (Indie track).

## License

[MIT](LICENSE). 98.css (MIT) and the MS Sans Serif font (CC BY-SA 3.0) keep their own licenses; see [legacy-demo/README.md](legacy-demo/README.md#créditos).
