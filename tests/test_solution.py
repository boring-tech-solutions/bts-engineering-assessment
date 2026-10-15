"""Candidate TODOs. Replace skips with two meaningful deterministic tests.

Use unittest.mock.patch("solution.ask_model", return_value=...) to supply model
text. Use side_effect=ModelError("Model request failed") to simulate failure.
Patch where the name is used (solution.ask_model), not model_client.ask_model.
No API key or installed SDK is required for these mocked tests.
"""

import unittest
from unittest.mock import patch

from model_client import ModelError
from solution import process_invoice


class InvoiceTests(unittest.TestCase):
    @unittest.skip("Candidate TODO: implement a success test with assertions")
    def test_success(self):
        # TODO: mock a model response, call process_invoice, assert fields/status.
        self.fail("Replace this placeholder")

    @unittest.skip("Candidate TODO: implement a failure or uncertainty test")
    def test_failure_or_uncertainty(self):
        # TODO: assert needs_review, a specific issue, and appropriate null fields.
        self.fail("Replace this placeholder")


if __name__ == "__main__":
    unittest.main()
