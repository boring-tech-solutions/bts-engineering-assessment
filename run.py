"""Batch runner. Guardrails here do not replace candidate error handling."""

import argparse
import json
import logging
import sys
from pathlib import Path

from solution import process_invoice

logger = logging.getLogger(__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run candidate solution on synthetic invoice text files")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional invoice text paths; defaults to fixtures/*.txt")
    args = parser.parse_args(argv)
    paths = args.paths or sorted(Path(__file__).with_name("fixtures").glob("*.txt"))
    if not paths:
        parser.error("No invoice files found.")
    failed = False
    for path in paths:
        try:
            result = process_invoice(path.read_text(encoding="utf-8"))
            record = {"file": path.name, "result": result}
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        except NotImplementedError:
            failed = True
            encoded = json.dumps({"file": path.name, "runner_error": "Candidate TODO: process_invoice is intentionally unfinished."})
        except Exception:
            failed = True
            encoded = json.dumps({"file": path.name, "runner_error": "Unhandled processing error; implement per-invoice error handling in solution.py."})
        print(encoded)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
