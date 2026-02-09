from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.common.java_index import JavaIndex
from tools.refactor.cli.remap_cleanup_owners import (
    _append_signature_promotions,
    _load_guard_promotion_rows,
    _remap_owner_columns,
)


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
                allow_file_remap=True,
            )

            self.assertEqual(owner_remaps, 1)
            self.assertEqual(file_remaps, 1)
            self.assertEqual(warnings, [])
            self.assertEqual(out[0]["owner_class"], "NewOwner")
            self.assertEqual(out[0]["file"], "new/NewOwner.java")

    def test_owner_remap_without_file_remap_for_callsite_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            src_dir.mkdir(parents=True)
            (src_dir / "callsite").mkdir()
            (src_dir / "new").mkdir()
            (src_dir / "callsite" / "Caller.java").write_text(
                "final class Caller { static void call() { OldOwner.foo(0); } }\n",
                encoding="utf-8",
            )
            (src_dir / "new" / "NewOwner.java").write_text(
                "final class NewOwner { static void foo() {} }\n",
                encoding="utf-8",
            )
            index = JavaIndex(src_dir)

            rows = [
                {
                    "file": "callsite/Caller.java",
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
                allow_file_remap=False,
            )

            self.assertEqual(owner_remaps, 1)
            self.assertEqual(file_remaps, 0)
            self.assertEqual(warnings, [])
            self.assertEqual(out[0]["owner_class"], "NewOwner")
            self.assertEqual(out[0]["file"], "callsite/Caller.java")

    def test_load_guard_promotion_rows_from_unified_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / ".refactor-plan"
            generated = plan_dir / "generated"
            src_dir = Path(tmp) / "src"
            generated.mkdir(parents=True)
            (src_dir / "client").mkdir(parents=True)
            (src_dir / "client" / "Foo.java").write_text(
                "final class Foo { int bar(int i) { if (i != 0) return 1; return 2; } }\n",
                encoding="utf-8",
            )
            (generated / "unified_cleanup_candidates.csv").write_text(
                (
                    "id,kind,file,owner,member,rule_type,match,dependency,confidence,action_hint,notes,gate_status,gate_reason\n"
                    "root:1,method,client/Foo.java,Foo,bar,guard_param,if (i != 0) return;,,high,signature_rewrites.drop_param+drop_statement_contains,n,auto,\n"
                    "caller:1,line_contains,client/Foo.java,Foo,bar,guard_dead_by_callers,bar[0] != 0,root:1,high,signature_rewrites.drop_statement_contains,n,auto,\n"
                ),
                encoding="utf-8",
            )
            rows = _load_guard_promotion_rows(plan_dir, JavaIndex(src_dir))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["file"], "client/Foo.java")
            self.assertEqual(rows[0]["owner"], "Foo")
            self.assertEqual(rows[0]["method"], "bar")
            self.assertEqual(rows[0]["op"], "drop_statement_contains")
            self.assertEqual(rows[0]["signature_before"], "int")
            self.assertEqual(rows[0]["signature_after"], "int")
            self.assertEqual(rows[0]["match_text"], "if (i != 0) return;")

    def test_append_signature_promotions_dedupes(self) -> None:
        existing = [
            {
                "file": "client/Foo.java",
                "owner": "Foo",
                "method": "bar",
                "op": "drop_statement_contains",
                "match_text": "if (i != 0) return;",
            }
        ]
        promoted = [
            {
                "file": "client/Foo.java",
                "owner": "Foo",
                "method": "bar",
                "op": "drop_statement_contains",
                "match_text": "if (i != 0) return;",
            },
            {
                "file": "client/Foo.java",
                "owner": "Foo",
                "method": "bar",
                "op": "drop_statement_contains",
                "match_text": "if (i != 1) return;",
            },
        ]
        out, added = _append_signature_promotions(existing, promoted)
        self.assertEqual(added, 1)
        self.assertEqual(len(out), 2)

    def test_guard_promotion_skips_block_style_guard_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / ".refactor-plan"
            generated = plan_dir / "generated"
            src_dir = Path(tmp) / "src"
            generated.mkdir(parents=True)
            (src_dir / "client").mkdir(parents=True)
            (src_dir / "client" / "Foo.java").write_text(
                "final class Foo { int bar(int i) { if (i != 0) { return 1; } return 2; } }\n",
                encoding="utf-8",
            )
            (generated / "unified_cleanup_candidates.csv").write_text(
                (
                    "id,kind,file,owner,member,rule_type,match,dependency,confidence,action_hint,notes,gate_status,gate_reason\n"
                    "root:1,method,client/Foo.java,Foo,bar,guard_param,if (i != 0) {,,high,signature_rewrites.drop_param+drop_statement_contains,n,auto,\n"
                    "caller:1,line_contains,client/Foo.java,Foo,bar,guard_dead_by_callers,bar[0] != 0,root:1,high,signature_rewrites.drop_statement_contains,n,auto,\n"
                ),
                encoding="utf-8",
            )
            rows = _load_guard_promotion_rows(plan_dir, JavaIndex(src_dir))
            self.assertEqual(rows, [])

    def test_guard_promotion_promotes_self_recursive_guard_without_caller_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / ".refactor-plan"
            generated = plan_dir / "generated"
            src_dir = Path(tmp) / "src"
            generated.mkdir(parents=True)
            (src_dir / "collections").mkdir(parents=True)
            (src_dir / "collections" / "SecondaryNode.java").write_text(
                (
                    "final class SecondaryNode {\n"
                    "  static void clearStaticState(int i) { if (i != 0) clearStaticState(-27); }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            (generated / "unified_cleanup_candidates.csv").write_text(
                (
                    "id,kind,file,owner,member,rule_type,match,dependency,confidence,action_hint,notes,gate_status,gate_reason\n"
                    "root:1,method,collections/SecondaryNode.java,SecondaryNode,clearStaticState,guard_param,if (i != 0) clearStaticState(-27);,,high,signature_rewrites.drop_param+drop_statement_contains,n,,\n"
                ),
                encoding="utf-8",
            )
            rows = _load_guard_promotion_rows(plan_dir, JavaIndex(src_dir))
            promoted = [
                row
                for row in rows
                if row.get("file") == "collections/SecondaryNode.java"
                and row.get("owner") == "SecondaryNode"
                and row.get("method") == "clearStaticState"
                and row.get("op") == "drop_statement_contains"
            ]
            self.assertEqual(len(promoted), 1)
            self.assertEqual(promoted[0]["match_text"], "if (i != 0) clearStaticState(-27);")

    def test_guard_promotion_skips_non_recursive_guard_without_caller_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / ".refactor-plan"
            generated = plan_dir / "generated"
            src_dir = Path(tmp) / "src"
            generated.mkdir(parents=True)
            (src_dir / "client").mkdir(parents=True)
            (src_dir / "client" / "Foo.java").write_text(
                (
                    "final class Foo {\n"
                    "  static void clearStaticState(int i) { if (i != 0) other(); }\n"
                    "  static void other() {}\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            (generated / "unified_cleanup_candidates.csv").write_text(
                (
                    "id,kind,file,owner,member,rule_type,match,dependency,confidence,action_hint,notes,gate_status,gate_reason\n"
                    "root:1,method,client/Foo.java,Foo,clearStaticState,guard_param,if (i != 0) other();,,high,signature_rewrites.drop_param+drop_statement_contains,n,,\n"
                ),
                encoding="utf-8",
            )
            rows = _load_guard_promotion_rows(plan_dir, JavaIndex(src_dir))
            self.assertEqual(rows, [])

    def test_guard_promotion_skips_recursive_guard_when_condition_turns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / ".refactor-plan"
            generated = plan_dir / "generated"
            src_dir = Path(tmp) / "src"
            generated.mkdir(parents=True)
            (src_dir / "collections").mkdir(parents=True)
            (src_dir / "collections" / "SecondaryNode.java").write_text(
                (
                    "final class SecondaryNode {\n"
                    "  static void clearStaticState(int i) { if (i != 0) clearStaticState(0); }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            (generated / "unified_cleanup_candidates.csv").write_text(
                (
                    "id,kind,file,owner,member,rule_type,match,dependency,confidence,action_hint,notes,gate_status,gate_reason\n"
                    "root:1,method,collections/SecondaryNode.java,SecondaryNode,clearStaticState,guard_param,if (i != 0) clearStaticState(0);,,high,signature_rewrites.drop_param+drop_statement_contains,n,,\n"
                ),
                encoding="utf-8",
            )
            rows = _load_guard_promotion_rows(plan_dir, JavaIndex(src_dir))
            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
