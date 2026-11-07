"""Measure UC-2 owner-request routing before agent processing.

Only synthetic owner prompts are submitted to the locally running FastAPI.
The three requests are recorded as ordinary demo chat runs and audits.
"""

from __future__ import annotations

import math
import sys

from backend.app.core.config import get_settings
from backend.scripts.verify_uc2 import VerificationError, _request_json


PROMPTS = (
    "Show products and stock",
    "Show recent orders",
    "Show products, stock and recent orders",
)
TARGET_SECONDS = 3.0
BASE_URL = "http://127.0.0.1:8000"


def main() -> int:
    settings = get_settings()
    if settings.llm_provider != "ollama" or not settings.demo_mode:
        print("FAIL: Set LLM_PROVIDER=ollama and DEMO_MODE=true in the project-root .env.", file=sys.stderr)
        return 1
    token = settings.shopsync_demo_owner_token.strip()
    if len(token) < 16 or token.startswith("CHANGE_ME"):
        print("FAIL: Set a private SHOPSYNC_DEMO_OWNER_TOKEN in .env.", file=sys.stderr)
        return 1
    try:
        health = _request_json(BASE_URL, "/api/health")
        if health.get("status") != "ok" or health.get("local_demo") is not True:
            raise VerificationError("Local FastAPI demo is not ready.")
        measured: list[float] = []
        for index, prompt in enumerate(PROMPTS, start=1):
            chat = _request_json(
                BASE_URL, "/api/owner/chat", token, {"message": prompt},
                timeout=max(15, settings.ollama_timeout_seconds + 5),
            )
            if chat.get("mode") != "ollama":
                raise VerificationError(
                    f"Request {index} used {chat.get('mode')!r} instead of Ollama. "
                    "Check Ollama, model availability, and the backend .env/restart."
                )
            seconds = chat.get("routing_seconds")
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                raise VerificationError("The backend did not return a valid routing_seconds metric; restart FastAPI with the Step 2 patch.")
            measured.append(seconds)
            verdict = "PASS" if seconds <= TARGET_SECONDS else "SLOW"
            print(f"{verdict} Request {index}: intent routing {seconds:.3f}s (target <= {TARGET_SECONDS:.1f}s; mode: ollama).")
        if any(seconds > TARGET_SECONDS for seconds in measured):
            print("FAIL: At least one local-model request exceeded the 3-second routing target; the target is not yet met on this machine.", file=sys.stderr)
            return 1
        print("PASS All three live UC-2 owner requests met the 3-second intent-routing target.")
        return 0
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
