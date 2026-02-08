from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.cli.ts_rename_identifiers import main as ts_rename_main


class TsRenameScopeMatchingTests(unittest.TestCase):
    def test_owner_member_and_signature_scoping(self) -> None:
        fixture_dir = Path(__file__).with_name("fixtures") / "ts_rename"
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src_dir = td_path / "src"
            src_dir.mkdir(parents=True, exist_ok=True)
            (src_dir / "ScopeCaseA.java").write_text(
                (fixture_dir / "scope_input.java").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            report_md = td_path / "rename-report.md"
            report_json = td_path / "rename-report.json"
            language_so = Path("build/ts-languages-java.so").resolve()
            cache_dir = td_path / "cache"

            rc = ts_rename_main(
                [
                    "--csv",
                    str((fixture_dir / "scope_mappings.csv").resolve()),
                    "--csv-dir",
                    str((td_path / "empty-csv-dir").resolve()),
                    "--src-dir",
                    str(src_dir.resolve()),
                    "--report",
                    str(report_md.resolve()),
                    "--report-json",
                    str(report_json.resolve()),
                    "--max-mappings",
                    "-1",
                    "--allow-non-obfuscated",
                    "--language-so",
                    str(language_so),
                    "--cache-dir",
                    str(cache_dir.resolve()),
                ]
            )
            self.assertEqual(rc, 0)

            actual = (src_dir / "ScopeCaseA.java").read_text(encoding="utf-8")
            expected = (fixture_dir / "scope_expected.java").read_text(encoding="utf-8")
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
