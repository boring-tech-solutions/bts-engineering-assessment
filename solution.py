"""Extract invoice fields with the model, then verify every value against the invoice text."""

import json
from decimal import Decimal

from model_client import ModelError, ask_model

FIELDS = ("supplier", "invoice_number", "date", "currency", "total")

# Statuses the model uses to say it could not give one trustworthy value.
PROBLEM_STATUSES = {
    "missing": "not found in the invoice",
    "conflicting": "conflicting values in the invoice",
    "unreadable": "unreadable in the invoice",
}

PROMPT = """You extract fields from one invoice. Reply with a single JSON object and nothing else.

Fields:
- "supplier": the business that issued the invoice (the seller), never the customer being billed.
- "invoice_number": the invoice's own identifier, copied character for character (keep case, punctuation and leading zeros).
- "date": the invoice issue date, copied exactly as written.
- "currency": a three-letter uppercase currency code written in the invoice (for example CAD or USD). A symbol such as "$", or a city, province or country, is not a currency code: use status "missing".
- "total": the final amount due, not a subtotal, tax or line item, as a number string copied as written (without "$").

For every field return {"value": ..., "status": ..., "reason": ...} where status is one of:
- "found": the invoice states exactly one value. Copy it exactly as written. Do not repair, reformat or normalise it, even if it looks wrong.
- "missing": the invoice does not state it. value must be null.
- "conflicting": the invoice states two or more different values for this field. value must be null; reason names the values.
- "unreadable": the value is damaged or illegible (for example OCR noise such as "1?5.00"). value must be null.
reason is a short explanation, or "" when status is "found".

Reply format:
{"supplier": {"value": "...", "status": "found", "reason": ""}, "invoice_number": {...}, "date": {...}, "currency": {...}, "total": {...}}

Invoice text:
<invoice>
"""


def build_prompt(invoice_text: str) -> str:
    return PROMPT + invoice_text + "\n</invoice>"


def process_invoice(invoice_text: str) -> dict:
    """Extract and validate one invoice. Never raises for model or response failures."""
    try:
        reply = ask_model(build_prompt(invoice_text))
    except ModelError as exc:
        return _failure(f"Model call failed: {exc}")
    except Exception as exc:
        return _failure(f"Model call failed unexpectedly ({type(exc).__name__}).")

    extraction = parse_reply(reply)
    if extraction is None:
        return _failure("Model response could not be parsed as a JSON object.")

    result, issues = {}, []
    for field in FIELDS:
        value, issue = _check_field(field, extraction.get(field), invoice_text)
        result[field] = value
        if issue:
            issues.append(f"{field}: {issue}")

    ready = not issues and all(result[field] is not None for field in FIELDS)
    result.update(status="ready" if ready else "needs_review", issues=issues)
    return result


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


def _check_field(field: str, entry, invoice_text: str):
    """Return (value, issue) for one field; value is None whenever issue is set."""
    if entry is None:
        return None, "missing from the model response."
    if isinstance(entry, dict):
        status, value, reason = entry.get("status"), entry.get("value"), entry.get("reason")
    else:
        status, value, reason = "found", entry, ""

    if status in PROBLEM_STATUSES:
        detail = f" ({reason.strip()})" if isinstance(reason, str) and reason.strip() else ""
        return None, f"{PROBLEM_STATUSES[status]}{detail}."
    if field == "total":
        return round(float(Decimal(str(value))), 2), None
    return _check_text(value, invoice_text)


def _check_text(value, invoice_text: str):
    """Text values must appear in the invoice exactly as returned, after trimming."""
    text = value.strip()
    if text not in invoice_text:
        return None, f"model value {text!r} does not appear in the invoice text."
    return text, None


def _failure(issue: str) -> dict:
    """Result for a failed model call or unusable response: no field can be trusted."""
    result = {field: None for field in FIELDS}
    result.update(status="needs_review", issues=[issue])
    return result
