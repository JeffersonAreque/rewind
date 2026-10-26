"""Smoke test for the model provider configured in .env.

Makes two calls to the active provider (REWIND_PROVIDER):
  1. plain text, to check the key, URL and model id;
  2. vision, sending one frame of samples/facturacion-demo.mp4 and asking
     the model to describe the screen, its fields and buttons.

Muse Spark Open always reasons before answering, so the reasoning is kept
apart from the final answer. Only the answer and the token counts are
printed; pass --reasoning to also see the reasoning.

The API key is never printed.

Usage:
    uv run python scripts/check_provider.py [--provider baseten] [--at 40] [--reasoning]
"""

import argparse
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from openai import OpenAI, OpenAIError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rewind.config import load_provider  # noqa: E402

VIDEO = ROOT / "samples" / "facturacion-demo.mp4"
THINK = re.compile(r"^\s*(?:<think>)?(.*?)</think>\s*", re.DOTALL)

TEXT_PROMPT = "Responde en una sola frase: ¿qué es un sistema de facturación?"
VISION_PROMPT = (
    "Esta es una captura de un programa antiguo de escritorio. Describe la pantalla: "
    "qué pantalla es, la lista de campos (etiqueta y valor actual, indicando si es de "
    "solo lectura) y la lista de botones y opciones. Responde en español, en listas cortas."
)


def split_reasoning(message) -> tuple[str, str]:
    """Return (reasoning, answer) from a chat message.

    Providers return the reasoning either in a separate field
    (reasoning_content / reasoning) or inline inside <think>...</think>.
    """
    extra = message.model_extra or {}
    reasoning = extra.get("reasoning_content") or extra.get("reasoning") or ""
    answer = message.content or ""
    match = THINK.match(answer)
    if match:
        reasoning = reasoning or match.group(1)
        answer = answer[match.end():]
    return reasoning.strip(), answer.strip()


def extract_frame(at: float) -> str:
    """Grab one frame of the demo video as a base64 PNG data URL."""
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "frame.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(VIDEO),
             "-frames:v", "1", str(png)],
            check=True,
        )
        data = base64.b64encode(png.read_bytes()).decode()
    return f"data:image/png;base64,{data}"


def report(title: str, response, show_reasoning: bool) -> None:
    reasoning, answer = split_reasoning(response.choices[0].message)
    usage = response.usage
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)

    print(f"\n=== {title} ===")
    if show_reasoning:
        print("--- razonamiento ---")
        print(reasoning or "(vacío)")
        print("--- respuesta ---")
    print(answer or "(vacía)")
    print(
        f"\n[tokens] entrada: {usage.prompt_tokens} | salida: {usage.completion_tokens}"
        f" (razonamiento: {reasoning_tokens if reasoning_tokens is not None else 'n/d'})"
        f" | total: {usage.total_tokens}"
    )
    print(f"[razonamiento] {len(reasoning)} caracteres, separado de la respuesta")
    print(f"[fin] {response.choices[0].finish_reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", help="override REWIND_PROVIDER")
    parser.add_argument("--at", type=float, default=40, help="second of the video to send (default 40)")
    parser.add_argument("--reasoning", action="store_true", help="also print the model's reasoning")
    args = parser.parse_args()

    config = load_provider(args.provider)
    if missing := config.missing():
        print(f"Falta en .env: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"Proveedor: {config.name} | modelo: {config.model}")

    client = OpenAI(base_url=config.base_url, api_key=config.api_key)
    try:
        text = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": TEXT_PROMPT}],
            max_tokens=2048,
        )
        report("1. Texto", text, args.reasoning)

        vision = client.chat.completions.create(
            model=config.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": extract_frame(args.at)}},
                ],
            }],
            max_tokens=4096,
        )
        report(f"2. Visión (cuadro del segundo {args.at:g})", vision, args.reasoning)
    except OpenAIError as exc:
        print(f"Error del proveedor: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
