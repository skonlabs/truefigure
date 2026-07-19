#!/usr/bin/env python3
"""Use-case conformance matrix runner.

Runs the per-UC conformance tests, maps each `test_uc_<id>` to its UC id, prints
the matrix (UC-id -> test id -> status), and ASSERTS every one of the 50 documented
use cases is COVERED and PASSING. Exit non-zero if any UC is missing or failing.

The document defines 50 UCs (the build-spec says 42; see docs/sdk_discrepancies.md
D4). Covering 50/50 satisfies the 42/42 mandate a fortiori.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# The 50 use cases the specification defines.
UC_IDS = [
    "UC-ONB-01", "UC-ONB-02", "UC-ONB-03", "UC-ONB-04",
    "UC-ACQ-01", "UC-ACQ-02", "UC-ACQ-03", "UC-ACQ-04", "UC-ACQ-05", "UC-ACQ-06", "UC-ACQ-07", "UC-ACQ-08", "UC-ACQ-09",
    "UC-IDQ-01", "UC-IDQ-02", "UC-IDQ-03",
    "UC-MEA-01", "UC-MEA-02", "UC-MEA-03", "UC-MEA-04", "UC-MEA-05",
    "UC-REP-01", "UC-REP-02", "UC-REP-03", "UC-REP-04",
    "UC-FIN-01", "UC-FIN-02", "UC-FIN-03", "UC-FIN-04", "UC-FIN-05",
    "UC-PIL-01", "UC-PIL-02", "UC-PIL-03", "UC-PIL-04",
    "UC-GOV-01", "UC-GOV-02", "UC-GOV-03", "UC-GOV-04", "UC-GOV-05",
    "UC-ECO-01", "UC-ECO-02", "UC-ECO-03", "UC-ECO-04", "UC-ECO-05", "UC-ECO-06",
    "UC-BEN-01", "UC-BEN-02",
    "UC-WFP-01", "UC-WFP-02",
    "UC-DEP-01",
]


def _test_to_uc(test_name: str) -> str | None:
    m = re.match(r"test_uc_([a-z]{3})_(\d{2})$", test_name)
    if not m:
        return None
    return f"UC-{m.group(1).upper()}-{m.group(2)}"


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(HERE / "test_uc.py"), "-p", "no:cacheprovider",
         "--tb=line", "-rA", "-q"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    status: dict[str, str] = {}
    for line in out.splitlines():
        m = re.search(r"(PASSED|FAILED|ERROR)\s+.*::(test_uc_[a-z]{3}_\d{2})", line)
        if not m:
            m = re.search(r"::(test_uc_[a-z]{3}_\d{2})\s+(PASSED|FAILED|ERROR)", line)
            if m:
                uc = _test_to_uc(m.group(1))
                if uc:
                    status[uc] = m.group(2)
            continue
        uc = _test_to_uc(m.group(2))
        if uc:
            status[uc] = m.group(1)

    print("=" * 60)
    print("USE-CASE CONFORMANCE MATRIX")
    print("=" * 60)
    ok = True
    for uc in UC_IDS:
        st = status.get(uc, "MISSING")
        mark = "PASS" if st == "PASSED" else st
        print(f"  {uc:12} test_uc_{uc[3:].lower().replace('-', '_'):10} {mark}")
        if st != "PASSED":
            ok = False
    covered = sum(1 for uc in UC_IDS if status.get(uc) == "PASSED")
    print("-" * 60)
    print(f"  {covered}/{len(UC_IDS)} use cases PASSING")
    print("=" * 60)
    if not ok or covered != len(UC_IDS):
        print("CONFORMANCE FAILED", file=sys.stderr)
        if proc.returncode != 0:
            print(out[-2000:], file=sys.stderr)
        return 1
    print("CONFORMANCE 50/50 — all use cases pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
