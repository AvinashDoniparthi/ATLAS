"""Optional Gemini adapter — natural-language helper only, never a source of truth.

Used by the router (a) to classify a question into the deterministic tool
schema when the keyword classifier is unsure, and (b) to rephrase the final
sentence from facts the backend already computed. Every output is validated by
the caller; any failure falls back to the deterministic path.

Requires ``GEMINI_API_KEY`` (env or .env). No SDK dependency: uses the REST API
via urllib so the harness runs without extra packages. If the key is absent the
adapter is never constructed (see stage1/atlas.py).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from backend.agent import prompts as _prompts_mod

log = logging.getLogger("atlas.llm")

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def _load_dotenv() -> None:
    if os.environ.get("GEMINI_API_KEY"):
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:  # noqa: BLE001 - optional
        pass


class GeminiAdapter:
    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 20.0):
        _load_dotenv()
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        self.model = model
        self.timeout = timeout
        self.tokens_used = 0
        self.calls = 0

    # ------------------------------------------------------------------ core
    def _generate(self, system: str, user: str, json_mode: bool = False) -> Optional[str]:
        body: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        req = urllib.request.Request(
            _ENDPOINT.format(model=self.model, key=self.api_key),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            log.warning("gemini call failed: %s", exc)
            return None
        self.calls += 1
        usage = data.get("usageMetadata") or {}
        self.tokens_used += int(usage.get("totalTokenCount") or 0)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None

    # -------------------------------------------------------------- helpers
    def classify(self, question: str, schema_hint: Any) -> Optional[dict]:
        """Return a dict conforming to the router's filter schema, or None."""
        system = getattr(_prompts_mod, "CLASSIFY_SYSTEM", "Classify the clinical-trial question into JSON.")
        hint = schema_hint if isinstance(schema_hint, str) else json.dumps(schema_hint, default=str)
        user = f"Allowed values:\n{hint}\n\nQuestion: {question}\n\nReturn only JSON."
        out = self._generate(system, user, json_mode=True)
        if not out:
            return None
        try:
            parsed = json.loads(out)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def rewrite(self, draft: str, facts: dict[str, Any]) -> tuple[Optional[str], int]:
        """Rephrase ``draft`` using only ``facts``. Returns (text, tokens used by this call).

        The router rejects any rewrite that introduces identifiers or numbers.
        """
        system = getattr(_prompts_mod, "REWRITE_SYSTEM", "Rewrite the sentence using only the given facts.")
        user = f"Facts (authoritative, do not change any number or ID):\n{json.dumps(facts, default=str)}\n\nDraft: {draft}"
        before = self.tokens_used
        out = self._generate(system, user)
        used = self.tokens_used - before
        if not out:
            return None, used
        out = out.strip()
        return (out or None), used
