"""Preload the configured local Ollama model before a UC-2 demo.

Run from the project root before starting the timed owner chat requests.
No owner prompt or authentication token is sent to Ollama.
"""

from __future__ import annotations

import json
import sys
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request

from backend.app.core.config import get_settings
from backend.app.infrastructure.integrations.ollama import _local_endpoint, _open_local, classify_local_request


class WarmupError(Exception):
    """A local model could not be preloaded."""


def warm_model(settings=None) -> float:
    """Load model weights and run one fictional classifier request before timing."""
    settings = settings or get_settings()
    if settings.llm_provider != "ollama":
        raise WarmupError("Set LLM_PROVIDER=ollama in the project-root .env file.")
    endpoint = _local_endpoint(settings.ollama_base_url)
    if endpoint is None:
        raise WarmupError("OLLAMA_BASE_URL must point to localhost or 127.0.0.1.")
    if not isinstance(settings.ollama_model, str) or not settings.ollama_model.strip():
        raise WarmupError("Set OLLAMA_MODEL to a model already pulled locally.")
    request = Request(
        endpoint,
        data=json.dumps({
            "model": settings.ollama_model,
            "messages": [],
            "stream": False,
            "keep_alive": "30m",
        }).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = perf_counter()
    try:
        with _open_local(request, timeout=max(180.0, settings.ollama_timeout_seconds)) as response:
            result = json.loads(response.read(65_536))
    except HTTPError as error:
        raise WarmupError(f"Ollama returned HTTP {error.code}. Check the model name and container.") from None
    except (URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError) as error:
        raise WarmupError(f"Cannot preload Ollama ({type(error).__name__}). Check docker compose ps.") from None
    if not isinstance(result, dict) or result.get("done") is not True:
        raise WarmupError("Ollama did not confirm that the model was loaded.")
    # Loading weights alone does not exercise the structured intent classifier.
    # Pay its first-inference cost here rather than on the first timed request.
    labels, mode = classify_local_request("Show products and stock", settings)
    if mode != "ollama" or not labels:
        raise WarmupError("Model loaded, but classifier warm-up failed. Check Ollama logs and model availability.")
    return perf_counter() - started


def main() -> int:
    try:
        seconds = warm_model()
        print(f"PASS Local Ollama model preloaded and classifier primed in {seconds:.2f}s; idle keep-alive: 30m.")
        print("Now run the UC-2 routing measurement while the model is loaded.")
        return 0
    except WarmupError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
