from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.cli.build_refactor_views import _read_plan_rows, _validate


class BuildRefactorViewsConflictTests(unittest.TestCase):
    def test_conflict_and_collision_warnings(self) -> None:
        fixture_dir = Path(__file__).with_name("fixtures") / "build_refactor_views"
        with tempfile.TemporaryDirectory() as td:
            plan_dir = Path(td) / "plan"
            src_dir = Path(td) / "src"
            plan_dir.mkdir(parents=True, exist_ok=True)
            src_dir.mkdir(parents=True, exist_ok=True)
            (src_dir / "collections" / "value").mkdir(parents=True, exist_ok=True)
            (src_dir / "collections" / "value" / "ScopeOwner.java").write_text(
                "final class ScopeOwner { int anInt1000; int anInt2000; int anInt3000; }\n",
                encoding="utf-8",
            )
            (plan_dir / "collections.rename.csv").write_text(
                (fixture_dir / "conflict_collision.rename.csv").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            rows = _read_plan_rows(plan_dir)
            errors, warnings = _validate(rows, src_dir)

            self.assertEqual(errors, [])
            expected = [
                line.strip()
                for line in (fixture_dir / "expected_warning_substrings.txt").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for needle in expected:
                self.assertTrue(any(needle in warning for warning in warnings), f"missing warning substring: {needle}")


if __name__ == "__main__":
    unittest.main()
