from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.cli.apply_refactor_cleanup import (
    SignatureCallsiteDropRule,
    _build_class_field_type_index,
    _rewrite_calls_for_signature_drop_rules,
)


class SignatureCallsiteDropTests(unittest.TestCase):
    def test_drops_args_for_for_loop_local_and_method_param(self) -> None:
        source = (
            "final class C {\n"
            "    void f() {\n"
            "        for (ReferenceNode n = first(); n != null; n = next()) {\n"
            "            if (!n.isSoft(-4)) {\n"
            "                use(n.getValue(100));\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    void g(ReferenceNode n) {\n"
            "        use(n.getValue(100));\n"
            "    }\n"
            "}\n"
        )
        rules = [
            SignatureCallsiteDropRule("isSoft", "ReferenceNode", 1, 0),
            SignatureCallsiteDropRule("getValue", "ReferenceNode", 1, 0),
        ]
        out, changed, matched = _rewrite_calls_for_signature_drop_rules(source, rules)
        self.assertTrue(changed)
        self.assertIn("n.isSoft()", out)
        self.assertIn("n.getValue()", out)
        self.assertIn(("isSoft", "ReferenceNode", 1, 0), matched)
        self.assertIn(("getValue", "ReferenceNode", 1, 0), matched)

    def test_drops_arg_for_static_class_field_receiver(self) -> None:
        source = (
            "final class SoftLruCache {\n"
            "    void f(ReferenceNode n) {\n"
            "        SoftwareMatrix.aClass246_5675.createSoftReferenceNode(3, n);\n"
            "    }\n"
            "}\n"
        )
        rules = [
            SignatureCallsiteDropRule("createSoftReferenceNode", "ReferenceNodeFactory", 2, 0),
            SignatureCallsiteDropRule("createSoftReferenceNode", "SoftReferenceNodeFactory", 2, 0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SoftwareMatrix.java").write_text(
                "/* Class101 - Decompiled by JODE */\n"
                "final class SoftwareMatrix {\n"
                "    static ReferenceNodeFactory aClass246_5675;\n"
                "}\n",
                encoding="utf-8",
            )
            class_field_types = _build_class_field_type_index(root)
        out, changed, matched = _rewrite_calls_for_signature_drop_rules(
            source,
            rules,
            class_field_types=class_field_types,
        )
        self.assertTrue(changed)
        self.assertIn("createSoftReferenceNode(n)", out)
        self.assertIn(("createSoftReferenceNode", "ReferenceNodeFactory", 2, 0), matched)


if __name__ == "__main__":
    unittest.main()
