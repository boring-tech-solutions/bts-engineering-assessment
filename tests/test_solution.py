"""Deterministic tests for process_invoice. solution.ask_model is always patched."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from model_client import ModelError
from solution import process_invoice

FIELDS = ("supplier", "invoice_number", "date", "currency", "total")
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

INVOICE_01_FIELDS = {
    "supplier": "Prairie Demo Supplies Ltd.",
    "invoice_number": "INV-000417",
    "date": "2026-09-14",
    "currency": "CAD",
    "total": "125.50",
}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def model_reply(**fields) -> str:
    """Build a model reply in the prompt's format; a field may be a value or a full entry dict."""
    entries = {}
    for name, value in fields.items():
        entries[name] = value if isinstance(value, dict) else {"value": value, "status": "found", "reason": ""}
    return json.dumps(entries)


def run_with_reply(invoice_text: str, reply: str) -> dict:
    with patch("solution.ask_model", return_value=reply):
        return process_invoice(invoice_text)


class ExtractionTests(unittest.TestCase):
    def test_clean_invoice_is_ready(self):
        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**INVOICE_01_FIELDS))

        self.assertEqual(result, {
            "supplier": "Prairie Demo Supplies Ltd.",
            "invoice_number": "INV-000417",
            "date": "2026-09-14",
            "currency": "CAD",
            "total": 125.5,
            "status": "ready",
            "issues": [],
        })
        self.assertIsInstance(result["total"], float)

    def test_reply_wrapped_in_code_fence_is_accepted(self):
        reply = "```json\n" + model_reply(**INVOICE_01_FIELDS) + "\n```"

        result = run_with_reply(fixture("invoice_01.txt"), reply)

        self.assertEqual(result["status"], "ready")

    def test_text_value_not_in_invoice_is_rejected_and_others_kept(self):
        fields = dict(INVOICE_01_FIELDS, invoice_number="INV-417")

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertIsNone(result["invoice_number"])
        self.assertEqual(result["supplier"], "Prairie Demo Supplies Ltd.")
        self.assertEqual(result["total"], 125.5)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["invoice_number: model value 'INV-417' does not appear in the invoice text."])

    def test_surrounding_whitespace_is_trimmed(self):
        fields = dict(INVOICE_01_FIELDS, supplier="  Prairie Demo Supplies Ltd.\n")

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertEqual(result["supplier"], "Prairie Demo Supplies Ltd.")
        self.assertEqual(result["status"], "ready")

    def test_conflict_reported_by_model_becomes_none(self):
        conflict = {"value": None, "status": "conflicting",
                    "reason": "subject says BTS-003 but body says BTS-030"}
        reply = model_reply(supplier="Aspen Demo Goods", invoice_number=conflict,
                            date="2026-09-16", currency="CAD", total=75)

        result = run_with_reply(fixture("invoice_05.txt"), reply)

        self.assertIsNone(result["invoice_number"])
        self.assertEqual(result["supplier"], "Aspen Demo Goods")
        self.assertEqual(result["total"], 75.0)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], [
            "invoice_number: conflicting values in the invoice (subject says BTS-003 but body says BTS-030)."
        ])

    def test_field_missing_from_reply_becomes_none(self):
        fields = dict(INVOICE_01_FIELDS)
        del fields["supplier"]

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertIsNone(result["supplier"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["supplier: missing from the model response."])


class ModelFailureTests(unittest.TestCase):
    def assert_all_fields_none(self, result):
        for field in FIELDS:
            self.assertIsNone(result[field], field)

    def test_model_error_returns_all_none_with_failure_issue(self):
        with patch("solution.ask_model", side_effect=ModelError("Model request failed")):
            result = process_invoice("Invoice #: INV-1")

        self.assert_all_fields_none(result)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["Model call failed: Model request failed"])

    def test_unexpected_exception_does_not_crash_the_batch(self):
        with patch("solution.ask_model", side_effect=TimeoutError("socket timed out")):
            result = process_invoice("Invoice #: INV-1")

        self.assert_all_fields_none(result)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["Model call failed unexpectedly (TimeoutError)."])

    def test_non_json_reply_returns_all_none_with_response_issue(self):
        with patch("solution.ask_model", return_value="Sorry, I cannot read this invoice."):
            result = process_invoice("Invoice #: INV-1")

        self.assert_all_fields_none(result)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["Model response could not be parsed as a JSON object."])

    def test_json_that_is_not_an_object_is_rejected(self):
        with patch("solution.ask_model", return_value='["Prairie Demo Supplies Ltd."]'):
            result = process_invoice("Invoice #: INV-1")

        self.assert_all_fields_none(result)
        self.assertEqual(result["issues"], ["Model response could not be parsed as a JSON object."])


if __name__ == "__main__":
    unittest.main()
