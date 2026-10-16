"""Extract invoice fields with the model, then verify every value against the invoice text."""

import json
import re
from datetime import date as Date
from decimal import Decimal, InvalidOperation

from model_client import ModelError, ask_model

FIELDS = ("supplier", "invoice_number", "date", "currency", "total")

DATE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")
CURRENCY_CODE = re.compile(r"[A-Z]{3}")
# A plain decimal amount, optionally after "$", not glued to letters, digits, "-", "?" or ".".
AMOUNT = re.compile(r"(?<![\w.?\-])\$?(\d+(?:\.\d+)?)(?![\w?\-]|\.\d)")

# Statuses the model uses to say it could not give one trustworthy value.
PROBLEM_STATUSES = {
    "missing": "not found in the invoice",
    "conflicting": "conflicting values in the invoice",
    "unreadable": "unreadable in the invoice",
}

PROMPT = """You extract fields from one invoice. Reply with a single JSON object and nothing else.

Fields:
- "supplier": the name of the business that issued the invoice (the seller), never the customer being billed. The business name only, without its address, city or province.
- "invoice_number": the invoice's own identifier, copied character for character (keep case, punctuation and leading zeros).
- "date": the invoice issue date, copied exactly as written.
- "currency": a three-letter uppercase currency code written in the invoice (for example CAD or USD). A symbol such as "$", or a city, province or country, is not a currency code: use status "missing".
- "total": the final amount due, not a subtotal, tax or line item, as a number string copied as written (without "$").

For every field return {"candidates": [...], "value": ..., "status": ..., "reason": ...}:
- "candidates": first, search the whole invoice (headers, subject lines, body text, sign-off) and list every place it states this field. Each candidate is the value only, copied exactly as written, without its label or surrounding words. Example: an email whose subject says "Invoice A-1" and whose body says "quote invoice number A-7" gives ["A-1", "A-7"].
- "status" is one of:
  - "found": every candidate is the same value. Copy it exactly as written. Do not repair, reformat or normalise it, even if it looks wrong.
  - "missing": the invoice does not state it. value must be null.
  - "conflicting": the candidates contain two or more different values. value must be null; reason names the values.
  - "unreadable": the value is damaged or illegible (for example OCR noise such as "1?5.00"). value must be null.
- "reason": a short explanation, or "" when status is "found".

Reply format:
{"supplier": {"candidates": ["..."], "value": "...", "status": "found", "reason": ""}, "invoice_number": {...}, "date": {...}, "currency": {...}, "total": {...}}

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
        candidates = _distinct_candidates(field, entry.get("candidates"))
    else:
        status, value, reason, candidates = "found", entry, "", []

    if status in PROBLEM_STATUSES:
        detail = reason.strip().rstrip(".") if isinstance(reason, str) else ""
        return None, f"{PROBLEM_STATUSES[status]}" + (f" ({detail})." if detail else ".")
    # The model may say "found" yet list two different values; trust the list over the label.
    if len(candidates) > 1:
        listed = ", ".join(repr(candidate) for candidate in candidates)
        return None, f"{PROBLEM_STATUSES['conflicting']} ({listed})."
    if status != "found":
        return None, f"model returned unknown status {status!r}."
    if value is None:
        return None, "model reported it as found but gave no value."
    if field == "total":
        return _check_total(value, invoice_text)

    text, issue = _check_text(value, invoice_text)
    if issue:
        return None, issue
    if field == "date":
        return _check_date(text)
    if field == "currency":
        return _check_currency(text, invoice_text)
    return text, None


def _distinct_candidates(field: str, candidates) -> list:
    """Distinct values the model saw for a field, in order; totals compare by decimal value."""
    if not isinstance(candidates, list):
        return []
    seen, distinct = set(), []
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, (str, int, float)):
            continue
        shown = str(candidate).strip()
        if not shown:
            continue
        key = shown
        if field == "total":
            try:
                key = Decimal(shown.removeprefix("$"))
            except InvalidOperation:
                pass
        if key not in seen:
            seen.add(key)
            distinct.append(shown)
    # "Invoice #: A-1" is A-1 with its label attached, not a second value.
    return [c for c in distinct if not any(o != c and _contains_token(c, o) for o in distinct)]


def _contains_token(text: str, token: str) -> bool:
    """True if token appears in text without letters or digits glued to either side."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text) is not None


def _check_text(value, invoice_text: str):
    """Text values must appear in the invoice exactly as returned, after trimming."""
    if not isinstance(value, str) or not value.strip():
        return None, f"expected a non-empty string, got {value!r}."
    text = value.strip()
    if text not in invoice_text:
        return None, f"model value {text!r} does not appear in the invoice text."
    return text, None


def _check_date(text: str):
    """Dates must already be YYYY-MM-DD and be a real calendar date; never repaired."""
    if not DATE_FORMAT.fullmatch(text):
        return None, f"{text!r} is not in YYYY-MM-DD format."
    try:
        Date.fromisoformat(text)
    except ValueError:
        return None, f"{text!r} is not a real calendar date."
    return text, None


def _check_currency(text: str, invoice_text: str):
    """Currency must be a three-letter uppercase code written as its own token in the invoice."""
    if not CURRENCY_CODE.fullmatch(text):
        return None, f"{text!r} is not a three-letter uppercase currency code."
    if not re.search(rf"(?<![A-Za-z]){text}(?![A-Za-z])", invoice_text):
        return None, f"model value {text!r} does not appear in the invoice text."
    return text, None


def _check_total(value, invoice_text: str):
    """Total must be a finite, nonnegative number equal by decimal value to an amount in the invoice."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, f"{value!r} is not a number."
    shown = str(value).strip()
    try:
        amount = Decimal(shown.removeprefix("$"))
    except InvalidOperation:
        return None, f"{value!r} is not a number."
    if not amount.is_finite():
        return None, f"{shown} is not a finite number."
    if amount < 0:
        return None, f"{shown} must not be negative."
    if amount not in _amounts_in(invoice_text):
        return None, f"{shown} does not match any amount written in the invoice."
    return round(float(amount), 2), None


def _amounts_in(invoice_text: str) -> set:
    """Decimal values of every standalone amount in the text; digits touching OCR noise do not count."""
    return {Decimal(match) for match in AMOUNT.findall(invoice_text)}


def _failure(issue: str) -> dict:
    """Result for a failed model call or unusable response: no field can be trusted."""
    result = {field: None for field in FIELDS}
    result.update(status="needs_review", issues=[issue])
    return result
