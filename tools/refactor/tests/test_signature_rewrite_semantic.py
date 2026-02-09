from __future__ import annotations

import unittest

from tools.refactor.cli.apply_refactor_cleanup import SignatureRewriteRule, _rewrite_signature_semantic


class SignatureRewriteSemanticTests(unittest.TestCase):
    def test_drop_param_and_guard_statement(self) -> None:
        source = (
            "final class C {\n"
            "    static void foo(int i, int keep) {\n"
            "        if (i != 0) return;\n"
            "        call(keep);\n"
            "    }\n"
            "}\n"
        )

        drop_param = SignatureRewriteRule(
            file="C.java",
            owner="C",
            method="foo",
            signature_before="int, int",
            signature_after="int",
            op="drop_param",
            param_index=0,
            new_name="",
            match_text="",
        )
        after_param, changed = _rewrite_signature_semantic(source, drop_param)
        self.assertTrue(changed)
        self.assertIn("foo(int keep)", after_param)

        drop_guard = SignatureRewriteRule(
            file="C.java",
            owner="C",
            method="foo",
            signature_before="int",
            signature_after="int",
            op="drop_statement_contains",
            param_index=-1,
            new_name="",
            match_text="if (i != 0) return;",
        )
        after_guard, changed = _rewrite_signature_semantic(after_param, drop_guard)
        self.assertTrue(changed)
        self.assertNotIn("if (i != 0) return;", after_guard)
        self.assertIn("call(keep);", after_guard)

    def test_drop_statement_contains_multiline_guard_block(self) -> None:
        source = (
            "final class C {\n"
            "    static void foo(int i) {\n"
            "        if (i != 0) {\n"
            "            callA();\n"
            "            callB();\n"
            "        }\n"
            "        callC();\n"
            "    }\n"
            "}\n"
        )
        drop_guard = SignatureRewriteRule(
            file="C.java",
            owner="C",
            method="foo",
            signature_before="int",
            signature_after="int",
            op="drop_statement_contains",
            param_index=-1,
            new_name="",
            match_text="if (i != 0) {",
        )
        out, changed = _rewrite_signature_semantic(source, drop_guard)
        self.assertTrue(changed)
        self.assertNotIn("if (i != 0)", out)
        self.assertIn("callC();", out)


if __name__ == "__main__":
    unittest.main()
