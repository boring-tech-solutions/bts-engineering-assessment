"""Extract invoice fields with the model, then verify every value against the invoice text."""

import json

from model_client import ModelError, ask_model

FIELDS = ("supplier", "invoice_number", "date", "currency", "total")


def process_invoice(invoice_text: str) -> dict:
    """Extract and validate one invoice. Never raises for model or response failures."""
    try:
        reply = ask_model(invoice_text)
    except ModelError as exc:
        return _failure(f"Model call failed: {exc}")
    except Exception as exc:
        return _failure(f"Model call failed unexpectedly ({type(exc).__name__}).")

    extraction = parse_reply(reply)
    if extraction is None:
        return _failure("Model response could not be parsed as a JSON object.")
    raise NotImplementedError


def parse_reply(reply) -> dict | None:
    """Return the JSON object in the model reply, tolerating code fences or extra prose."""
    if not isinstance(reply, str):
        return None
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        parsed = json.loads(reply[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _failure(issue: str) -> dict:
    """Result for a failed model call or unusable response: no field can be trusted."""
    result = {field: None for field in FIELDS}
    result.update(status="needs_review", issues=[issue])
    return result
