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

    def test_conflict_found_in_candidates_overrides_found_status(self):
        number = {"value": "BTS-030", "status": "found", "reason": "", "candidates": ["BTS-003", "BTS-030"]}
        reply = model_reply(supplier="Aspen Demo Goods", invoice_number=number,
                            date="2026-09-16", currency="CAD", total="75.00")

        result = run_with_reply(fixture("invoice_05.txt"), reply)

        self.assertIsNone(result["invoice_number"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], [
            "invoice_number: conflicting values in the invoice ('BTS-003', 'BTS-030')."
        ])

    def test_repeated_candidate_with_same_value_is_not_a_conflict(self):
        total = {"value": "42.00", "status": "found", "reason": "", "candidates": ["$42.00", "42.00", " 42 "]}
        supplier = {"value": "River Demo Services", "status": "found", "reason": "",
                    "candidates": ["River Demo Services", "River Demo Services "]}
        reply = model_reply(supplier=supplier, invoice_number="BTS-002", date="2026-09-15",
                            currency={"value": None, "status": "missing", "reason": ""}, total=total)

        result = run_with_reply(fixture("invoice_03.txt"), reply)

        self.assertEqual(result["supplier"], "River Demo Services")
        self.assertEqual(result["total"], 42.0)
        self.assertEqual(result["issues"], ["currency: not found in the invoice."])

    def test_candidate_with_label_text_is_the_same_value(self):
        fields = dict(INVOICE_01_FIELDS)
        fields["invoice_number"] = {"value": "INV-000417", "status": "found", "reason": "",
                                    "candidates": ["INV-000417", "Invoice #: INV-000417"]}
        fields["total"] = {"value": "125.50", "status": "found", "reason": "",
                           "candidates": ["125.50", "Amount due (CAD)   125.50"]}

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertEqual(result["status"], "ready")

    def test_longer_identifier_is_not_mistaken_for_a_label(self):
        number = {"value": "BTS-03", "status": "found", "reason": "", "candidates": ["BTS-03", "BTS-030"]}
        reply = model_reply(supplier="Aspen Demo Goods", invoice_number=number,
                            date="2026-09-16", currency="CAD", total="75.00")

        result = run_with_reply(fixture("invoice_05.txt"), reply)

        self.assertIsNone(result["invoice_number"])
        self.assertEqual(result["issues"], [
            "invoice_number: conflicting values in the invoice ('BTS-03', 'BTS-030')."
        ])

    def test_model_conflict_is_kept_even_when_its_candidate_list_is_incomplete(self):
        # Live run on e05: the model said "conflicting" but listed 310.00 twice and left out 301.00.
        invoice = (FIXTURES.parent / "tests" / "eval_invoices" / "e05_conflicting_totals.txt").read_text(encoding="utf-8")
        for candidates in (["310.00", "310.00"], ["310.00", "Total due: 310.00"]):
            with self.subTest(candidates=candidates):
                total = {"value": None, "status": "conflicting", "reason": "310.00 vs 301.00", "candidates": candidates}
                reply = model_reply(supplier="Juniper Demo Catering", invoice_number="JDC-5521",
                                    date="2026-04-18", currency="USD", total=total)

                result = run_with_reply(invoice, reply)

                self.assertIsNone(result["total"])
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["issues"], ["total: conflicting values in the invoice (310.00 vs 301.00)."])

    def test_model_conflict_with_a_single_candidate_is_kept(self):
        total = {"value": None, "status": "conflicting", "reason": "two totals", "candidates": ["125.50"]}

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, total=total)))

        self.assertIsNone(result["total"])
        self.assertEqual(result["issues"], ["total: conflicting values in the invoice (two totals)."])

    def test_identifier_extended_by_a_suffix_is_a_conflict(self):
        for longer in ("INV-000417-B", "INV-000417/2"):
            with self.subTest(longer=longer):
                number = {"value": "INV-000417", "status": "found", "reason": "",
                          "candidates": ["INV-000417", longer]}

                result = run_with_reply(fixture("invoice_01.txt"),
                                        model_reply(**dict(INVOICE_01_FIELDS, invoice_number=number)))

                self.assertIsNone(result["invoice_number"])
                self.assertEqual(result["issues"], [
                    f"invoice_number: conflicting values in the invoice ('INV-000417', '{longer}')."
                ])

    def test_reason_ending_with_period_is_not_doubled(self):
        unreadable = {"value": None, "status": "unreadable", "reason": "Amount is OCR noise."}
        reply = model_reply(supplier="Spruce Demo Equipment", invoice_number="BTS-005",
                            date="2026-09-18", currency="CAD", total=unreadable)

        result = run_with_reply(fixture("invoice_04.txt"), reply)

        self.assertEqual(result["issues"], ["total: unreadable in the invoice (Amount is OCR noise)."])

    def test_unreadable_field_without_reason_gets_generic_issue(self):
        unreadable = {"value": None, "status": "unreadable", "reason": ""}
        reply = model_reply(supplier="Spruce Demo Equipment", invoice_number="BTS-005",
                            date="2026-09-18", currency="CAD", total=unreadable)

        result = run_with_reply(fixture("invoice_04.txt"), reply)

        self.assertEqual(result["currency"], "CAD")
        self.assertIsNone(result["total"])
        self.assertEqual(result["issues"], ["total: unreadable in the invoice."])

    def test_unknown_status_is_not_trusted(self):
        fields = dict(INVOICE_01_FIELDS, date={"value": "2026-09-14", "status": "probably", "reason": ""})

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertIsNone(result["date"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["date: model returned unknown status 'probably'."])

    def test_every_problem_is_listed_in_field_order(self):
        reply = model_reply(supplier="River Demo Services", invoice_number="BTS-200",
                            date="2026-09-15", currency={"value": None, "status": "missing", "reason": "only $ is shown"},
                            total="42.00")

        result = run_with_reply(fixture("invoice_03.txt"), reply)

        self.assertEqual(result["issues"], [
            "invoice_number: model value 'BTS-200' does not appear in the invoice text.",
            "currency: not found in the invoice (only $ is shown).",
        ])

    def test_prompt_sends_the_invoice_text(self):
        invoice = fixture("invoice_01.txt")
        with patch("solution.ask_model", return_value=model_reply(**INVOICE_01_FIELDS)) as ask:
            process_invoice(invoice)

        prompt = ask.call_args.args[0]
        self.assertIn(invoice, prompt)
        self.assertIn("conflicting", prompt)

    def test_field_missing_from_reply_becomes_none(self):
        fields = dict(INVOICE_01_FIELDS)
        del fields["supplier"]

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertIsNone(result["supplier"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["supplier: missing from the model response."])


class FieldRuleTests(unittest.TestCase):
    def assert_only_field_rejected(self, result, field, issue):
        self.assertIsNone(result[field])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], [f"{field}: {issue}"])

    def test_non_string_text_value_is_rejected(self):
        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, supplier=123)))

        self.assert_only_field_rejected(result, "supplier", "expected a non-empty string, got 123.")

    def test_blank_text_value_is_rejected(self):
        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, supplier="  ")))

        self.assert_only_field_rejected(result, "supplier", "expected a non-empty string, got '  '.")

    def test_fragment_of_a_longer_value_is_rejected(self):
        result = run_with_reply(fixture("invoice_01.txt"),
                                model_reply(**dict(INVOICE_01_FIELDS, invoice_number="000417")))

        self.assert_only_field_rejected(result, "invoice_number",
                                        "model value '000417' does not appear in the invoice text.")

    def test_fragment_cut_before_a_non_ascii_letter_is_rejected(self):
        invoice = "Caf\u00e9 Demo Roasters\nInvoice: CDR_7\nDate: 2026-09-14\nTotal due: 10.00 CAD\n"
        for field, fragment in (("supplier", "Caf"), ("invoice_number", "CDR")):
            with self.subTest(field=field):
                fields = dict(supplier="Caf\u00e9 Demo Roasters", invoice_number="CDR_7", date="2026-09-14",
                              currency="CAD", total="10.00")
                fields[field] = fragment

                result = run_with_reply(invoice, model_reply(**fields))

                self.assert_only_field_rejected(result, field,
                                                f"model value {fragment!r} does not appear in the invoice text.")

    def test_impossible_calendar_date_is_rejected(self):
        reply = model_reply(supplier="Meadow Demo Consulting", invoice_number="BTS-004",
                            date="2026-02-30", currency="USD", total="90.00")

        result = run_with_reply(fixture("invoice_02.txt"), reply)

        self.assertEqual(result["invoice_number"], "BTS-004")
        self.assertEqual(result["total"], 90.0)
        self.assert_only_field_rejected(result, "date", "'2026-02-30' is not a real calendar date.")

    def test_date_in_another_format_is_not_converted(self):
        invoice = "Acme Ltd.\nInvoice: A-1\nDate: 14/09/2026\nTotal due: 10.00 CAD\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="A-1", date="14/09/2026",
                            currency="CAD", total="10.00")

        result = run_with_reply(invoice, reply)

        self.assert_only_field_rejected(result, "date", "'14/09/2026' is not in YYYY-MM-DD format.")

    def test_date_with_non_ascii_digits_is_not_yyyy_mm_dd(self):
        fullwidth_date = "\uff12\uff10\uff12\uff16-09-14"
        invoice = f"Acme Ltd.\nInvoice: A-1\nDate: {fullwidth_date}\nTotal due: 10.00 CAD\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="A-1", date=fullwidth_date,
                            currency="CAD", total="10.00")

        result = run_with_reply(invoice, reply)

        self.assert_only_field_rejected(result, "date", f"{fullwidth_date!r} is not in YYYY-MM-DD format.")

    def test_amount_with_non_ascii_digits_is_not_a_plain_amount(self):
        fullwidth_total = "\uff14\uff12.00"
        invoice = f"Acme Ltd.\nInvoice: A-1\nDate: 2026-09-14\nTotal due: {fullwidth_total} CAD\n"
        for total, issue in ((fullwidth_total, f"{fullwidth_total!r} is not a plain decimal amount."),
                             ("42.00", "42.00 does not match any amount written in the invoice.")):
            with self.subTest(total=total):
                reply = model_reply(supplier="Acme Ltd.", invoice_number="A-1", date="2026-09-14",
                                    currency="CAD", total=total)

                result = run_with_reply(invoice, reply)

                self.assert_only_field_rejected(result, "total", issue)

    def test_currency_symbol_is_not_a_currency_code(self):
        reply = model_reply(supplier="River Demo Services", invoice_number="BTS-002",
                            date="2026-09-15", currency="$", total="42.00")

        result = run_with_reply(fixture("invoice_03.txt"), reply)

        self.assertEqual(result["total"], 42.0)
        self.assert_only_field_rejected(result, "currency", "'$' is not a three-letter uppercase currency code.")

    def test_currency_inferred_from_location_is_rejected(self):
        reply = model_reply(supplier="River Demo Services", invoice_number="BTS-002",
                            date="2026-09-15", currency="CAD", total="42.00")

        result = run_with_reply(fixture("invoice_03.txt"), reply)

        self.assert_only_field_rejected(result, "currency", "model value 'CAD' does not appear in the invoice text.")

    def test_currency_must_be_a_standalone_code_in_the_text(self):
        invoice = "Acme Ltd.\nInvoice: CADX-1\nDate: 2026-09-14\nTotal due: 10.00\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="CADX-1", date="2026-09-14",
                            currency="CAD", total="10.00")

        result = run_with_reply(invoice, reply)

        self.assert_only_field_rejected(result, "currency", "model value 'CAD' does not appear in the invoice text.")

    def test_currency_code_glued_to_an_identifier_is_rejected(self):
        invoice = "Acme Ltd.\nInvoice: CAD-1\nDate: 2026-09-14\nTotal due: 10.00\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="CAD-1", date="2026-09-14",
                            currency="CAD", total="10.00")

        result = run_with_reply(invoice, reply)

        self.assert_only_field_rejected(result, "currency", "model value 'CAD' does not appear in the invoice text.")

    def test_three_capital_letters_that_are_not_a_currency_are_rejected(self):
        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, currency="GST")))

        self.assert_only_field_rejected(result, "currency", "'GST' is not a recognised ISO 4217 currency code.")

    def test_total_guessed_from_ocr_damage_is_rejected(self):
        reply = model_reply(supplier="Spruce Demo Equipment", invoice_number="BTS-005",
                            date="2026-09-18", currency="CAD", total="125.00")

        result = run_with_reply(fixture("invoice_04.txt"), reply)

        self.assert_only_field_rejected(result, "total", "125.00 does not match any amount written in the invoice.")

    def test_digits_inside_a_damaged_amount_are_not_an_amount(self):
        reply = model_reply(supplier="Spruce Demo Equipment", invoice_number="BTS-005",
                            date="2026-09-18", currency="CAD", total="5.00")

        result = run_with_reply(fixture("invoice_04.txt"), reply)

        self.assert_only_field_rejected(result, "total", "5.00 does not match any amount written in the invoice.")

    def test_total_as_number_or_dollar_string_matches_by_decimal_value(self):
        for total in (42, 42.0, "42", "$42.00"):
            with self.subTest(total=total):
                reply = model_reply(supplier="River Demo Services", invoice_number="BTS-002",
                                    date="2026-09-15", currency={"value": None, "status": "missing", "reason": ""},
                                    total=total)

                result = run_with_reply(fixture("invoice_03.txt"), reply)

                self.assertEqual(result["total"], 42.0)
                self.assertIsInstance(result["total"], float)

    def test_invalid_total_values_are_rejected(self):
        cases = {
            True: "True is not a number.",
            "-90.00": "-90.00 must not be negative.",
            "NaN": "NaN is not a finite number.",
            "Infinity": "Infinity is not a finite number.",
            "ninety": "'ninety' is not a number.",
            "9.000E1": "'9.000E1' is not a plain decimal amount.",
            "9_0.00": "'9_0.00' is not a plain decimal amount.",
            None: "model reported it as found but gave no value.",
        }
        for total, issue in cases.items():
            with self.subTest(total=total):
                reply = model_reply(supplier="Meadow Demo Consulting", invoice_number="BTS-004",
                                    date="2026-02-28", currency="USD", total=total)

                result = run_with_reply(fixture("invoice_02.txt").replace("2026-02-30", "2026-02-28"), reply)

                self.assert_only_field_rejected(result, "total", issue)


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


    def test_object_without_any_invoice_field_is_a_response_failure(self):
        for reply in ("{}", '{"answer": "see attached"}'):
            with self.subTest(reply=reply):
                result = run_with_reply(fixture("invoice_01.txt"), reply)

                self.assert_all_fields_none(result)
                self.assertEqual(result["issues"], ["Model response did not contain any invoice fields."])

    def test_braces_in_prose_before_the_json_are_skipped(self):
        reply = "Note {draft}: here is the result. " + model_reply(**INVOICE_01_FIELDS)

        result = run_with_reply(fixture("invoice_01.txt"), reply)

        self.assertEqual(result["status"], "ready")

    def test_unhashable_status_does_not_crash(self):
        fields = dict(INVOICE_01_FIELDS, date={"value": "2026-09-14", "status": [], "reason": ""})

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**fields))

        self.assertIsNone(result["date"])
        self.assertEqual(result["issues"], ["date: model returned unknown status []."])

    def test_signaling_nan_candidate_does_not_crash(self):
        total = {"value": "125.50", "status": "found", "reason": "", "candidates": ["125.50", "sNaN"]}

        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, total=total)))

        self.assertIsNone(result["total"])
        self.assertEqual(result["status"], "needs_review")

    def test_unexpected_failure_while_validating_is_a_response_failure(self):
        with patch("solution._check_field", side_effect=RuntimeError("boom")):
            result = run_with_reply(fixture("invoice_01.txt"), model_reply(**INVOICE_01_FIELDS))

        self.assert_all_fields_none(result)
        self.assertEqual(result["issues"], ["Model response had an unexpected shape (RuntimeError)."])


if __name__ == "__main__":
    unittest.main()
