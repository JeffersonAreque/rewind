"""Provider settings read from .env.

Every provider exposes an OpenAI-compatible API, so a provider is just
three values: base URL, API key and model id. Switching providers means
changing REWIND_PROVIDER in .env; nothing in the code changes.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URLS = {
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "baseten": "https://inference.baseten.co/v1",
}
PROVIDERS = tuple(DEFAULT_BASE_URLS)

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str

    def missing(self) -> list[str]:
        """Names of the .env variables that still need a value."""
        prefix = self.name.upper()
        fields = {"BASE_URL": self.base_url, "API_KEY": self.api_key, "MODEL": self.model}
        return [f"{prefix}_{key}" for key, value in fields.items() if not value]


def load_provider(name: str | None = None) -> ProviderConfig:
    load_dotenv(ROOT / ".env")
    name = (name or os.getenv("REWIND_PROVIDER", "fireworks")).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"REWIND_PROVIDER must be one of {PROVIDERS}, got {name!r}")
    prefix = name.upper()
    return ProviderConfig(
        name=name,
        base_url=os.getenv(f"{prefix}_BASE_URL", "").strip() or DEFAULT_BASE_URLS[name],
        api_key=os.getenv(f"{prefix}_API_KEY", "").strip(),
        model=os.getenv(f"{prefix}_MODEL", "").strip(),
    )
