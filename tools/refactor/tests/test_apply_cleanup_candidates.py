from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ApplyCleanupCandidatesTests(unittest.TestCase):
    def test_high_confidence_candidate_is_imported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidates = tmp_path / "cleanup_candidates.csv"
            drop_members = tmp_path / "drop_members.csv"
            src_dir = tmp_path / "client" / "refactor"
            src_dir.mkdir(parents=True)

            with candidates.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["file", "kind", "name", "phase", "status", "confidence", "category", "notes"])
                w.writerow(["collections/value/IntNode.java", "field", "anInt9500", "cleanup", "proposed", "high", "field-drop", "unused"])

            with drop_members.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["file", "kind", "name", "phase", "status", "notes"])

            cmd = [
                sys.executable,
                "-m",
                "tools.refactor.cleanup.apply_cleanup_candidates",
                "--candidates",
                str(candidates),
                "--drop-members",
                str(drop_members),
                "--src-dir",
                str(src_dir),
                "--confidence",
                "high",
            ]
            subprocess.run(cmd, check=True, cwd=str(Path.cwd()))

            rows = list(csv.DictReader(drop_members.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["file"], "collections/value/IntNode.java")
            self.assertEqual(rows[0]["kind"], "field")
            self.assertEqual(rows[0]["name"], "anInt9500")
            self.assertEqual(rows[0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
