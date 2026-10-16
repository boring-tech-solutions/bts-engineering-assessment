"""Seeded fuzz test: random model replies must never break the README result contract.

Each case builds a reply from fragments of the invoice text, hostile values and
broken JSON, then checks invariants that must hold for every result. The seed is
fixed, so a failure always reproduces; the failing case is printed.
"""

import datetime
import json
import math
import random
import re
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from solution import FIELDS, ISO_4217, parse_reply, process_invoice
from test_eval_invoices import EVAL_DIR, LABELS, careful_reply

ROOT = Path(__file__).resolve().parent.parent
INVOICES = [path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("fixtures/*.txt"))]
INVOICES += [path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("tests/eval_invoices/*.txt"))]
# Non-ASCII digits that \d matches in Python regular expressions.
INVOICES.append("Acme Ltd.\nInvoice A-1\nDate: \uff12\uff10\uff12\uff16-09-14\nTotal: \uff14\uff12.00 USD\n42.00\n")

ASCII_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ASCII_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
HOSTILE_VALUES = [
    None, True, False, 0, -1, 1.5, -0.0, float("nan"), float("inf"), 1e308, [], {}, ["x"], {"value": 1},
    "", " ", "\n", "CAD", "USD", "GST", "cad", "$", "C$", "2026-02-30", "2026-13-01", "2026-9-14",
    "NaN", "-Infinity", "sNaN", "1e2", "-1.00", "0", "0.00", "$0", "12_5.50", "1,250.00",
    "\uff11\uff12\uff15.50", "\uff12\uff10\uff12\uff16-09-14", "INV-000417\n", "null", "{", "}",
]
STATUSES = ["found", "found", "found", "missing", "conflicting", "unreadable", "Found", "", None, [], {}, 1]


def random_value(rng: random.Random, text: str):
    roll = rng.random()
    if roll < 0.45:
        return rng.choice(re.findall(r"\S+", text) or [""])
    if roll < 0.65:
        start = rng.randrange(len(text))
        return text[start:start + rng.randint(1, 30)]
    if roll < 0.75:
        return rng.choice(re.findall(r"\d+(?:\.\d+)?", text) or ["0"])
    return rng.choice(HOSTILE_VALUES)


def random_entry(rng: random.Random, text: str):
    if rng.random() < 0.1:
        return random_value(rng, text)
    entry = {"value": random_value(rng, text), "status": rng.choice(STATUSES), "reason": rng.choice(["", "x.", None, 3])}
    if rng.random() < 0.15:
        entry["candidates"] = [entry["value"], entry["value"]]  # An incomplete list that looks like agreement.
    elif rng.random() < 0.6:
        entry["candidates"] = [random_value(rng, text) for _ in range(rng.randint(0, 3))]
    elif rng.random() < 0.2:
        entry["candidates"] = random_value(rng, text)
    return entry


def corrupted_careful_reply(rng: random.Random, name: str, text: str):
    """A careful reply with 0 to 2 fields replaced by random entries; returns (reply, untouched)."""
    fields = json.loads(careful_reply(name))
    corrupted = rng.sample(FIELDS, rng.choice([0, 0, 1, 1, 2]))
    for field in corrupted:
        fields[field] = random_entry(rng, text)
    return json.dumps(fields), not corrupted


def random_reply(rng: random.Random, text: str):
    fields = {name: random_entry(rng, text) for name in FIELDS if rng.random() < 0.9}
    if rng.random() < 0.1:
        fields["extra"] = random_value(rng, text)
    body = json.dumps(fields)
    roll = rng.random()
    if roll < 0.05:
        return body[: rng.randrange(len(body) + 1)]
    if roll < 0.10:
        return "```json\n" + body + "\n```"
    if roll < 0.13:
        return "Here {is} the answer: " + body + " {done}"
    if roll < 0.15:
        return json.dumps([fields])
    if roll < 0.17:
        return rng.choice(["", "null", "42", "{}", "{{}}", "not json", None, 42])
    return body


class ContractFuzzTests(unittest.TestCase):
    CASES = 4000

    def assert_only_stricter_than_model(self, result: dict, reply):
        """Code may reject what the model found, never accept a field the model did not report as found."""
        extraction = parse_reply(reply)
        if extraction is None:
            return
        for field in FIELDS:
            entry = extraction.get(field)
            if not (isinstance(entry, dict) and entry.get("status") == "found"):
                self.assertIsNone(result[field], f"{field} accepted although the model gave {entry!r}")

    def assert_contract(self, result: dict, text: str):
        self.assertEqual(set(result), {*FIELDS, "status", "issues"})
        self.assertIn(result["status"], ("ready", "needs_review"))
        self.assertIsInstance(result["issues"], list)
        for issue in result["issues"]:
            self.assertIsInstance(issue, str)
            self.assertTrue(issue.strip())
        complete = all(result[field] is not None for field in FIELDS)
        self.assertEqual(result["status"] == "ready", complete and result["issues"] == [])
        if result["status"] == "needs_review":
            self.assertTrue(result["issues"])

        for field in ("supplier", "invoice_number", "date", "currency"):
            value = result[field]
            if value is not None:
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                self.assertEqual(value, value.strip())
                self.assertIn(value, text)
        if result["date"] is not None:
            self.assertRegex(result["date"], ASCII_DATE)
            datetime.date.fromisoformat(result["date"])
        if result["currency"] is not None:
            self.assertIn(result["currency"], ISO_4217)
        total = result["total"]
        if total is not None:
            self.assertIs(type(total), float)
            self.assertTrue(math.isfinite(total))
            self.assertGreaterEqual(total, 0)
            self.assertEqual(round(total, 2), total)
            written = {Decimal(number) for number in ASCII_NUMBER.findall(text)}
            self.assertTrue(any(abs(Decimal(str(total)) - amount) < Decimal("0.005") for amount in written),
                            f"total {total} is not an amount written in the invoice")
        json.dumps(result, allow_nan=False)

    def test_random_replies_never_break_the_contract(self):
        rng = random.Random(20261016)
        for case in range(self.CASES):
            text = rng.choice(INVOICES)
            reply = random_reply(rng, text)
            with self.subTest(case=case, reply=reply):
                with patch("solution.ask_model", return_value=reply):
                    result = process_invoice(text)
                self.assert_contract(result, text)
                self.assert_only_stricter_than_model(result, reply)

    def test_corrupted_careful_replies_never_break_the_contract(self):
        rng = random.Random(16102026)
        statuses = []
        for case in range(self.CASES):
            name = rng.choice(sorted(LABELS))
            text = (EVAL_DIR / name).read_text(encoding="utf-8")
            reply, untouched = corrupted_careful_reply(rng, name, text)
            with self.subTest(case=case, invoice=name, reply=reply):
                with patch("solution.ask_model", return_value=reply):
                    result = process_invoice(text)
                self.assert_contract(result, text)
                self.assert_only_stricter_than_model(result, reply)
                if untouched and LABELS[name].get("status") == "ready":
                    self.assertEqual(result["status"], "ready")
            statuses.append(result["status"])
        # Guard against a generator that never reaches the accepting paths.
        self.assertGreater(statuses.count("ready"), self.CASES // 10)


if __name__ == "__main__":
    unittest.main()
