from __future__ import annotations

import unittest

from tools.refactor.cleanup.manual_guard_regression import build_guard_regression_report


class ManualGuardRegressionTests(unittest.TestCase):
    def test_manual_guard_regression_coverage(self) -> None:
        report = build_guard_regression_report()
        self.assertEqual(report.total_cases, 10)
        self.assertEqual(report.covered_cases, 8)
        self.assertEqual(report.out_of_scope_cases, 2)
        self.assertEqual(report.missed_cases, 0, msg=f"Missed cases: {report.missed_names}")
        self.assertEqual(
            len(report.out_of_scope_hit_names),
            0,
            msg=f"Unexpected out-of-scope detector hits: {report.out_of_scope_hit_names}",
        )


if __name__ == "__main__":
    unittest.main()
