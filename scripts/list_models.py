#!/usr/bin/env python3
"""List Gemini models available to your API key that support generateContent.

Use this to choose YOUVERIFY_EXTRACTION_MODEL / YOUVERIFY_VERIFICATION_MODEL
instead of trusting hardcoded model IDs, which change frequently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from google import genai

from app.core.config import get_settings


def main() -> int:
    key = get_settings().gemini_api_key
    if key is None:
        print("GEMINI_API_KEY is not set (see .env.example).", file=sys.stderr)
        return 1
    client = genai.Client(api_key=key.get_secret_value())
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" in actions:
            print(f"{model.name}\t{getattr(model, 'display_name', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
