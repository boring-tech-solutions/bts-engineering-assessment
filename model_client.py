"""Supplied model transport. Candidates do not need to change this file.

SDK reference: https://developers.openai.com/api/reference/python
"""

import argparse
import os
from pathlib import Path


class ModelError(RuntimeError):
    """A configuration, transport, or unusable model-response error."""


def ask_model(prompt: str) -> str:
    """Return model text or raise ModelError with a secret-free message.

    Tests should patch solution.ask_model to return deterministic strings or
    raise ModelError. The SDK and dotenv are imported only on a real call.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ModelError("A non-empty prompt is required.")
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).with_name(".env"), override=False)
        from openai import OpenAI
    except ImportError:
        raise ModelError("Install the supplied requirements before a live call.") from None

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not api_key or not model:
        raise ModelError("Set OPENAI_API_KEY and OPENAI_MODEL for a live call.")

    try:
        with OpenAI(api_key=api_key, timeout=20.0, max_retries=0) as client:
            response = client.responses.create(model=model, input=prompt, store=False)
            if getattr(response, "status", None) != "completed":
                raise ModelError("Model response did not complete.")
            result = response.output_text
            if not isinstance(result, str) or not result.strip():
                raise ModelError("Model returned no usable text.")
            return result
    except ModelError:
        raise
    except Exception:
        # Do not expose provider errors, request contents, or credentials.
        raise ModelError("Model request failed. Check configuration or retry later.") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supplied OpenAI helper")
    parser.add_argument("--live-smoke", action="store_true", help="Make one real API call, which may incur cost")
    args = parser.parse_args()
    if not args.live_smoke:
        parser.error("Pass --live-smoke explicitly to make a real API call.")
    try:
        print(ask_model("Reply with exactly: OK"))
    except ModelError as exc:
        parser.exit(1, f"{exc}\n")
