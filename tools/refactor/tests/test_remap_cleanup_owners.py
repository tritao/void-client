from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.common.java_index import JavaIndex
from tools.refactor.cli.remap_cleanup_owners import _remap_owner_columns


class RemapCleanupOwnersTests(unittest.TestCase):
    def test_owner_and_file_are_remapped_when_member_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "old").mkdir()
            (src_dir / "new").mkdir()
            (src_dir / "old" / "OldOwner.java").write_text(
                "final class OldOwner { static void other() {} }\n",
                encoding="utf-8",
            )
            (src_dir / "new" / "NewOwner.java").write_text(
                "final class NewOwner { static void foo() {} }\n",
                encoding="utf-8",
            )
            index = JavaIndex(src_dir)

            rows = [
                {
                    "file": "old/OldOwner.java",
                    "owner_class": "OldOwner",
                    "method": "foo",
                }
            ]
            aliases: dict[tuple[str, str, str], set[str]] = {}
            move_targets = {("method", "OldOwner", "foo"): {"NewOwner"}}

            out, owner_remaps, file_remaps, warnings = _remap_owner_columns(
                rows=rows,
                aliases=aliases,
                move_targets=move_targets,
                java_index=index,
            )

            self.assertEqual(owner_remaps, 1)
            self.assertEqual(file_remaps, 1)
            self.assertEqual(warnings, [])
            self.assertEqual(out[0]["owner_class"], "NewOwner")
            self.assertEqual(out[0]["file"], "new/NewOwner.java")


if __name__ == "__main__":
    unittest.main()
