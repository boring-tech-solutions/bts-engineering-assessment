"""Deterministic tests for process_invoice. solution.ask_model is always patched."""

import unittest
from unittest.mock import patch

from model_client import ModelError
from solution import process_invoice

FIELDS = ("supplier", "invoice_number", "date", "currency", "total")


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
