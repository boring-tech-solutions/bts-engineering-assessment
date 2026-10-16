"""Labelled evaluation invoices, replayed with scripted model replies (no API key needed).

Two questions per invoice: does a correct reply produce the labelled result, and
does validation still catch the trap when the model wrongly reports "found"?
evaluate.py asks the same questions of the real model.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from solution import FIELDS, process_invoice

EVAL_DIR = Path(__file__).resolve().parent / "eval_invoices"
LABELS = {name: label for name, label in json.loads((EVAL_DIR / "expected.json").read_text(encoding="utf-8")).items()
          if not name.startswith("_")}

# What a careful model would write for each field it cannot trust, keyed by invoice and field.
PROBLEM_ENTRIES = {
    ("e04_conflicting_dates.txt", "date"): {"status": "conflicting", "candidates": ["2026-05-02", "2026-05-20"]},
    ("e05_conflicting_totals.txt", "total"): {"status": "conflicting", "candidates": ["310.00", "301.00"]},
    ("e06_conflicting_currency.txt", "currency"): {"status": "conflicting", "candidates": ["USD", "CAD"]},
    ("e08_lowercase_currency.txt", "currency"): {"status": "missing", "candidates": []},
    ("e09_symbol_currency.txt", "currency"): {"status": "missing", "candidates": []},
    ("e10_written_date.txt", "date"): {"status": "missing", "candidates": []},
    ("e11_thousands_separator.txt", "total"): {"status": "unreadable", "candidates": ["1,250.00"]},
    ("e15_credit_note.txt", "total"): {"status": "found", "value": "-45.00", "candidates": ["-45.00"]},
    ("e18_total_in_words.txt", "total"): {"status": "missing", "candidates": []},
    ("e19_invalid_month.txt", "date"): {"status": "found", "value": "2026-13-01", "candidates": ["2026-13-01"]},
}

# The model wrongly says "found" and copies the raw text; validation alone must still reject it.
FOUND_TRAPS = [
    ("e08_lowercase_currency.txt", "currency", "cad", "'cad' is not a three-letter uppercase currency code."),
    ("e09_symbol_currency.txt", "currency", "C$", "'C$' is not a three-letter uppercase currency code."),
    ("e09_symbol_currency.txt", "currency", "CA$", "'CA$' is not a three-letter uppercase currency code."),
    ("e10_written_date.txt", "date", "14 September 2026", "'14 September 2026' is not in YYYY-MM-DD format."),
    ("e10_written_date.txt", "date", "2026-09-14", "model value '2026-09-14' does not appear in the invoice text."),
    ("e11_thousands_separator.txt", "total", "1,250.00", "'1,250.00' is not a number."),
    ("e11_thousands_separator.txt", "total", "1250.00", "1250.00 does not match any amount written in the invoice."),
    ("e15_credit_note.txt", "total", "-45.00", "-45.00 must not be negative."),
    ("e18_total_in_words.txt", "total", "eighty dollars", "'eighty dollars' is not a number."),
    ("e18_total_in_words.txt", "total", "80.00", "80.00 does not match any amount written in the invoice."),
    ("e19_invalid_month.txt", "date", "2026-13-01", "'2026-13-01' is not a real calendar date."),
    ("e08_lowercase_currency.txt", "currency", "CAD", "model value 'CAD' does not appear in the invoice text."),
    ("e09_symbol_currency.txt", "currency", "CAD", "model value 'CAD' does not appear in the invoice text."),
]


def careful_reply(name: str) -> str:
    """The reply a careful model would give: labelled values as found, known problems reported."""
    label = LABELS[name]
    reply = {}
    for field in FIELDS:
        problem = PROBLEM_ENTRIES.get((name, field))
        if problem:
            reply[field] = {"value": None, "reason": "", **problem}
        elif label.get(field) is not None:
            value = label[field]
            reply[field] = {"value": value, "status": "found", "reason": "", "candidates": [value]}
    return json.dumps(reply)


def run(name: str, reply: str) -> dict:
    with patch("solution.ask_model", return_value=reply):
        return process_invoice((EVAL_DIR / name).read_text(encoding="utf-8"))


class CarefulModelTests(unittest.TestCase):
    """A careful model reply must give exactly the labelled result for every evaluation invoice."""

    def test_careful_reply_matches_every_label(self):
        for name, label in LABELS.items():
            if any(label.get(field) is None and (name, field) not in PROBLEM_ENTRIES
                   for field in FIELDS if field in label):
                continue  # Labels that need the model to report "missing" for unlabelled reasons.
            with self.subTest(invoice=name):
                result = run(name, careful_reply(name))

                if "known_limit" in label:
                    # Pinned: labelled with the true answer, but known to go to review.
                    self.assertEqual(result["status"], "needs_review", label["known_limit"])
                    continue
                for key in (*FIELDS, "status"):
                    if key in label:
                        self.assertEqual(result[key], label[key], key)

    def test_ready_invoices_are_ready_with_no_issues(self):
        for name, label in LABELS.items():
            if label.get("status") != "ready" or "known_limit" in label:
                continue
            with self.subTest(invoice=name):
                result = run(name, careful_reply(name))

                self.assertEqual(result["status"], "ready", result["issues"])
                self.assertEqual(result["issues"], [])


class FoundTrapTests(unittest.TestCase):
    """Validation must catch each trap even when the model wrongly reports the raw text as found."""

    def test_trap_reported_as_found_is_still_rejected(self):
        for name, field, written, issue in FOUND_TRAPS:
            with self.subTest(invoice=name, field=field, value=written):
                reply = json.loads(careful_reply(name))
                reply[field] = {"value": written, "status": "found", "reason": "", "candidates": [written]}

                result = run(name, json.dumps(reply))

                self.assertIsNone(result[field])
                self.assertEqual(result["status"], "needs_review")
                self.assertIn(f"{field}: {issue}", result["issues"])


if __name__ == "__main__":
    unittest.main()
