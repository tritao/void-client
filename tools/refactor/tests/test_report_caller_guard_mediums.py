from __future__ import annotations

import tempfile
import unittest
import csv
from pathlib import Path

from tools.refactor.cleanup.report_caller_guard_mediums import run


class ReportCallerGuardMediumsTests(unittest.TestCase):
    def test_writes_reason_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_csv = root / "in.csv"
            out_md = root / "out.md"
            with in_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "id",
                        "kind",
                        "file",
                        "owner",
                        "member",
                        "rule_type",
                        "match",
                        "dependency",
                        "confidence",
                        "action_hint",
                        "notes",
                        "gate_status",
                        "gate_reason",
                    ]
                )
                w.writerow(
                    [
                        "a",
                        "line_contains",
                        "a/A.java",
                        "A",
                        "m",
                        "guard_dead_by_callers",
                        "m",
                        "",
                        "medium",
                        "",
                        "",
                        "review",
                        "non_private_method",
                    ]
                )
                w.writerow(
                    [
                        "b",
                        "line_contains",
                        "a/B.java",
                        "B",
                        "m",
                        "guard_dead_by_callers",
                        "m",
                        "",
                        "medium",
                        "",
                        "",
                        "review",
                        "overloaded_method",
                    ]
                )
                w.writerow(
                    [
                        "c",
                        "line_contains",
                        "a/B.java",
                        "B",
                        "m",
                        "guard_dead_by_callers",
                        "m",
                        "",
                        "medium",
                        "",
                        "",
                        "review",
                        "overloaded_method",
                    ]
                )
                w.writerow(
                    [
                        "d",
                        "line_contains",
                        "a/C.java",
                        "C",
                        "m",
                        "guard_param",
                        "m",
                        "",
                        "high",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
            rc = run(["--in-csv", str(in_csv), "--out-md", str(out_md)])
            self.assertEqual(rc, 0)
            text = out_md.read_text(encoding="utf-8")
            self.assertIn("Rows: 3", text)
            self.assertIn("`overloaded_method`", text)
            self.assertIn("`non_private_method`", text)


if __name__ == "__main__":
    unittest.main()
