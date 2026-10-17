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

    def test_bare_value_without_a_status_is_not_accepted(self):
        invoice, _ = eval_reply("e05_conflicting_totals.txt")
        reply = json.loads(model_reply(supplier="Juniper Demo Catering", invoice_number="JDC-5521",
                                       date="2026-04-18", currency="USD"))
        reply["total"] = "310.00"

        result = run_with_reply(invoice, json.dumps(reply))

        self.assertIsNone(result["total"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], ["total: model gave a value without a status."])

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

    def test_currency_code_inside_an_identifier_is_not_a_currency(self):
        for number in ("CADX-1", "CAD-1"):
            with self.subTest(invoice_number=number):
                invoice = f"Acme Ltd.\nInvoice: {number}\nDate: 2026-09-14\nTotal due: 10.00\n"
                reply = model_reply(supplier="Acme Ltd.", invoice_number=number, date="2026-09-14",
                                    currency="CAD", total="10.00")

                result = run_with_reply(invoice, reply)

                self.assert_only_field_rejected(result, "currency",
                                                "model value 'CAD' does not appear in the invoice text.")

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
        # No known reply reaches this fallback any more (the fuzz test tries thousands), so the
        # only way to prove it works is to make a validation step fail on purpose.
        with patch("solution._check_field", side_effect=RuntimeError("boom")):
            result = run_with_reply(fixture("invoice_01.txt"), model_reply(**INVOICE_01_FIELDS))

        self.assert_all_fields_none(result)
        self.assertEqual(result["issues"], ["Model response had an unexpected shape (RuntimeError)."])


EVAL_DIR = FIXTURES.parent / "tests" / "eval_invoices"
EVAL_LABELS = json.loads((EVAL_DIR / "expected.json").read_text(encoding="utf-8"))


def eval_reply(name: str, **overrides) -> tuple[str, str]:
    """(invoice text, model reply) for an evaluation invoice: its labelled values, with overrides."""
    fields = {field: EVAL_LABELS[name][field] for field in FIELDS if EVAL_LABELS[name].get(field) is not None}
    fields.update(overrides)
    return (EVAL_DIR / name).read_text(encoding="utf-8"), model_reply(**fields)


class RoleLabelTests(unittest.TestCase):
    """A value that appears only under a label for a different field is the wrong value."""

    def assert_wrong_role(self, result, field, issue):
        self.assertIsNone(result[field])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], [f"{field}: {issue}"])

    def test_customer_on_the_same_line_as_bill_to_is_not_the_supplier(self):
        result = run_with_reply(fixture("invoice_01.txt"),
                                model_reply(**dict(INVOICE_01_FIELDS, supplier="Boreal Demo Co-op")))

        self.assert_wrong_role(result, "supplier",
                               "'Boreal Demo Co-op' only appears after 'Bill to:', which labels a different field.")

    def test_subtotal_is_not_the_total(self):
        result = run_with_reply(fixture("invoice_01.txt"), model_reply(**dict(INVOICE_01_FIELDS, total="119.52")))

        self.assert_wrong_role(result, "total", "119.52 only appears after 'Subtotal', which labels a different field.")

    def test_customer_under_a_bill_to_heading_is_not_the_supplier(self):
        result = run_with_reply(*eval_reply("e01_bill_to_first.txt", supplier="Northwind Demo Traders"))

        self.assert_wrong_role(result, "supplier",
                               "'Northwind Demo Traders' only appears after 'BILL TO', which labels a different field.")

    def test_due_date_is_not_the_invoice_date(self):
        result = run_with_reply(*eval_reply("e03_issue_and_due_date.txt", date="2026-07-31"))

        self.assert_wrong_role(result, "date", "'2026-07-31' only appears after 'Due date:', which labels a different field.")

    def test_customer_with_a_similar_name_is_not_the_supplier(self):
        holdings = "Harbour Demo Marine Services Holdings"
        result = run_with_reply(*eval_reply("e22_customer_named_like_supplier.txt", supplier=holdings))

        self.assert_wrong_role(result, "supplier", f"{holdings!r} only appears after 'Invoice for:', which labels a different field.")

    def test_value_under_its_own_label_is_kept_when_it_also_appears_elsewhere(self):
        # "Harbour Demo Marine Services" follows both "Invoice from:" and "Invoice for:".
        result = run_with_reply(*eval_reply("e22_customer_named_like_supplier.txt"))

        self.assertEqual(result["supplier"], "Harbour Demo Marine Services")
        self.assertEqual(result["status"], "ready")

    def test_nearest_column_decides_the_label(self):
        result = run_with_reply(*eval_reply("e23_two_column_header.txt"))

        self.assertEqual(result["supplier"], "Alder Demo Medical Supply")
        self.assertEqual(result["date"], "2026-09-30")
        self.assertEqual(result["status"], "ready")

    def test_wrong_values_in_a_two_column_header_are_still_caught(self):
        cases = (("supplier", "Cypress Demo Clinic", "'Cypress Demo Clinic' only appears after 'Bill to:'"),
                 ("date", "2026-10-30", "'2026-10-30' only appears after 'Due date:'"),
                 ("total", "0.60", "0.60 only appears after 'GST 5%'"))
        for field, value, issue in cases:
            with self.subTest(field=field):
                result = run_with_reply(*eval_reply("e23_two_column_header.txt", **{field: value}))

                self.assert_wrong_role(result, field, f"{issue}, which labels a different field.")

    def test_total_label_that_mentions_tax_is_still_a_total(self):
        result = run_with_reply(*eval_reply("e23_two_column_header.txt", total="12.60"))

        self.assertEqual(result["total"], 12.6)
        self.assertEqual(result["status"], "ready")

    def test_remit_to_names_the_supplier(self):
        result = run_with_reply(*eval_reply("e13_supplier_in_footer.txt"))

        self.assertEqual(result["supplier"], "Cedar Demo Print Shop")
        self.assertEqual(result["status"], "ready")

    def test_customer_after_a_bare_to_label_is_not_the_supplier(self):
        reply = model_reply(supplier="Aspen Demo Society", invoice_number="BTS-002", date="2026-09-15",
                            currency={"value": None, "status": "missing", "reason": ""}, total="42.00")

        result = run_with_reply(fixture("invoice_03.txt"), reply)

        self.assertIsNone(result["supplier"])
        self.assertIn("supplier: 'Aspen Demo Society' only appears after 'To:', which labels a different field.",
                      result["issues"])

    @staticmethod
    def total_line_invoice(label: str) -> tuple[str, str]:
        """An invoice whose only statement of 77.70 is the given label, and a reply choosing 77.70."""
        invoice = f"Acme Ltd.\nInvoice: A-1\nDate: 2026-09-14\nCurrency: CAD\n\nWidgets            40.00\n{label}            77.70\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="A-1", date="2026-09-14", currency="CAD", total="77.70")
        return invoice, reply

    def test_total_labels_that_only_mention_another_field_are_kept(self):
        for label in ("Amount payable now", "Net after discount", "Cost after tax"):
            with self.subTest(label=label):
                result = run_with_reply(*self.total_line_invoice(label))

                self.assertEqual(result["total"], 77.7)
                self.assertEqual(result["status"], "ready")

    def test_total_labels_that_negate_the_total_are_rejected(self):
        for label in ("Total excluding tax", "Total already paid", "Total (excl. GST)"):
            with self.subTest(label=label):
                result = run_with_reply(*self.total_line_invoice(label))

                self.assert_wrong_role(result, "total", f"77.70 only appears after {label!r}, which labels a different field.")

    def test_known_limit_total_named_after_a_tax_is_accepted(self):
        # These are the tax amount, but the label reads like a total; only evaluation can catch this.
        for label in ("Total VAT", "Total GST", "Total tax"):
            with self.subTest(label=label):
                result = run_with_reply(*self.total_line_invoice(label))

                self.assertEqual(result["status"], "ready")

    def test_known_limit_total_label_starting_with_a_tax_word_goes_to_review(self):
        # Real totals, rejected because the label starts with another amount's name. This is the
        # safe direction, so it is accepted rather than patched with more label rules.
        for label in ("Tax invoice total", "GST inclusive total", "VAT incl. total", "Deposit balance due"):
            with self.subTest(label=label):
                result = run_with_reply(*self.total_line_invoice(label))

                self.assertIsNone(result["total"])
                self.assertEqual(result["status"], "needs_review")

    @staticmethod
    def number_line_invoice(label: str) -> tuple[str, str]:
        """An invoice whose only statement of 5520 is the given label, and a reply choosing 5520."""
        invoice = f"Acme Ltd.\nInvoice: A-1\nDate: 2026-09-14\nTotal due: 77.70 CAD\n{label} 5520\n"
        reply = model_reply(supplier="Acme Ltd.", invoice_number="5520", date="2026-09-14", currency="CAD", total="77.70")
        return invoice, reply

    def test_label_naming_another_field_is_rejected_even_if_it_also_names_this_one(self):
        for label in ("Invoice PO number:", "Invoice for order #:", "PO / invoice ref:"):
            with self.subTest(label=label):
                result = run_with_reply(*self.number_line_invoice(label))

                self.assert_wrong_role(result, "invoice_number",
                                       f"'5520' only appears after {label!r}, which labels a different field.")

    def test_ambiguous_label_goes_to_review_even_when_the_value_is_right(self):
        # Each label names the right field and another one. Sending an ambiguous label to review
        # is the deliberate, safe trade-off, shown here for every field the label check covers.
        base = dict(supplier="Acme Ltd.", invoice_number="A-1", date="2026-09-14", currency="CAD", total="77.70")
        cases = (("invoice_number", "Customer invoice no: A-1", "Acme Ltd.\nDate: 2026-09-14\n"),
                 ("date", "Invoice or delivery date: 2026-09-14", "Acme Ltd.\nInvoice: A-1\n"),
                 ("supplier", "Seller (client account team): Acme Ltd.", "Invoice: A-1\nDate: 2026-09-14\n"))
        for field, line, rest in cases:
            with self.subTest(field=field):
                result = run_with_reply(f"{rest}Total due: 77.70 CAD\n{line}\n", model_reply(**base))

                self.assertIsNone(result[field])
                self.assertEqual(result["status"], "needs_review")

    def test_known_limit_sentence_mentioning_an_order_sends_the_invoice_number_to_review(self):
        # On a line without columns the label is all the text before the value, so an earlier
        # "order" in a sentence rejects a real invoice number. Narrowing the label at "." or ","
        # would fix this but lets "PO no. 5520" through, so the safe direction is kept.
        for line in ("We appreciate your order. Your invoice number is INV-88.",
                     "Thanks for the order; please quote INV-88 when paying."):
            with self.subTest(line=line):
                invoice = f"Acme Ltd.\nDate: 2026-09-14\nTotal due: 77.70 CAD\n{line}\n"
                reply = model_reply(supplier="Acme Ltd.", invoice_number="INV-88", date="2026-09-14",
                                    currency="CAD", total="77.70")

                result = run_with_reply(invoice, reply)

                self.assertIsNone(result["invoice_number"])
                self.assertEqual(result["status"], "needs_review")

    def test_label_narrowed_at_an_abbreviation_would_let_a_po_number_through(self):
        # Guards the reason the sentence case above is not fixed by splitting labels at ".".
        for line in ("PO no. 5520", "Order no. 5520", "Customer acct. 5520"):
            with self.subTest(line=line):
                result = run_with_reply(*self.number_line_invoice(line.removesuffix(" 5520")))

                self.assertIsNone(result["invoice_number"])
                self.assertEqual(result["status"], "needs_review")

    def test_po_and_account_numbers_are_not_the_invoice_number(self):
        for number, label in (("5520", "PO number:"), ("0007", "Customer account:")):
            with self.subTest(number=number):
                result = run_with_reply(*eval_reply("e14_po_and_leading_zeros.txt", invoice_number=number))

                self.assert_wrong_role(result, "invoice_number",
                                       f"{number!r} only appears after {label!r}, which labels a different field.")


if __name__ == "__main__":
    unittest.main()
