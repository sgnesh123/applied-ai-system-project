"""LLM clients for the AI task suggester.

Adapted from the BugHound starter. Every client has the same interface:
    complete(system_prompt, user_prompt) -> str
An empty string means "no usable answer", which makes the suggester fall back
to default tasks instead of crashing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("pawpal_ai")

DEFAULT_MODEL = "gemini-3.5-flash"


class MockClient:
    """Offline stand-in used when no API key is set.

    Always returns an empty string, so the app runs end to end without a key
    and shows the fallback path.
    """

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        logger.info("MockClient in use (no GEMINI_API_KEY); returning empty response")
        return ""


class GeminiClient:
    """Minimal Gemini API wrapper. Needs google-genai and GEMINI_API_KEY."""

    def __init__(self, model_name: str | None = None, temperature: float = 0.2):
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY. Copy .env.example to .env and set GEMINI_API_KEY=...")

        # Imported here so tests and offline mode don't need the package.
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.temperature = float(temperature)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send one request. Returns "" on any API error so the caller can fall back."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=f"{system_prompt}\n\n{user_prompt}".strip(),
                config={"temperature": self.temperature},
            )
            return response.text or ""
        except Exception as exc:  # network errors, quota, blocked content, ...
            logger.error("Gemini request failed: %s", exc)
            return ""


def get_client():
    """Return a GeminiClient if a key is configured, otherwise a MockClient."""
    if os.getenv("GEMINI_API_KEY", "").strip():
        try:
            return GeminiClient()
        except Exception as exc:
            logger.error("Could not create GeminiClient, using MockClient: %s", exc)
    return MockClient()
