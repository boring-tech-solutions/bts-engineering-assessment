"""Live evaluation: run process_invoice on labelled invoices with the real model and score it.

Makes real API calls (one per invoice per run). The most important number is
false_ready: results marked "ready" that disagree with the label, because no
human would look at them again.
"""

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from solution import FIELDS, process_invoice

EVAL_DIR = Path(__file__).with_name("tests") / "eval_invoices"


def score(expected: dict, result: dict) -> list[str]:
    """Return one line per labelled key where the result disagrees with the label."""
    return [
        f"{key}: expected {expected[key]!r}, got {result[key]!r}"
        for key in (*FIELDS, "status")
        if key in expected and result[key] != expected[key]
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score the real model on labelled evaluation invoices")
    parser.add_argument("--runs", type=int, default=3, help="Runs per invoice, to expose inconsistency")
    parser.add_argument("--only", nargs="*", default=[], help="Evaluate only these file names")
    parser.add_argument("--show-issues", action="store_true", help="Print issues for every mismatching run")
    args = parser.parse_args(argv)

    labels = json.loads((EVAL_DIR / "expected.json").read_text(encoding="utf-8"))
    names = [name for name in labels if not name.startswith("_") and (not args.only or name in args.only)]
    jobs = [(name, run) for name in names for run in range(args.runs)]
    texts = {name: (EVAL_DIR / name).read_text(encoding="utf-8") for name in names}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: process_invoice(texts[job[0]]), jobs))

    totals = Counter()
    for name in names:
        runs = [result for (job_name, _), result in zip(jobs, results) if job_name == name]
        mismatches = [score(labels[name], result) for result in runs]
        false_ready = sum(1 for result, diff in zip(runs, mismatches) if diff and result["status"] == "ready")
        passed = sum(1 for diff in mismatches if not diff)
        totals.update(runs=len(runs), passed=passed, false_ready=false_ready)

        flag = "FALSE READY" if false_ready else ("ok" if passed == len(runs) else "mismatch")
        print(f"{name:42} {passed}/{len(runs)} {flag}")
        for diff, result in zip(mismatches, runs):
            for line in diff:
                print(f"    - {line}")
            if diff and args.show_issues:
                print(f"      issues: {result['issues']}")

    print(f"\npassed {totals['passed']}/{totals['runs']} runs, false_ready={totals['false_ready']}")
    return 1 if totals["false_ready"] else 0


if __name__ == "__main__":
    sys.exit(main())
