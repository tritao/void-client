from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.refactor.cleanup.candidate_detector import detect_unified_cleanup_candidates


class UnifiedCleanupDetectorTests(unittest.TestCase):
    def _write(self, root: Path, rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_detects_param_guard_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/reference/SoftReferenceNodeFactory.java",
                (
                    "final class SoftReferenceNodeFactory {\n"
                    "    static Object createSoftReferenceNode(int i, Object node) {\n"
                    "        if (i != 3) return null;\n"
                    "        return node;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src)
            hits = [c for c in candidates if c.rule_type == "guard_param"]
            self.assertEqual(len(hits), 1)
            self.assertIn("if (i != 3) return null;", hits[0].match)

    def test_detects_param_guard_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/value/ShortNode.java",
                (
                    "final class ShortNode {\n"
                    "    static void clearStaticState(int i) {\n"
                    "        if (i != -4587) clearStaticState(-101);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src)
            guard_param = [c for c in candidates if c.rule_type == "guard_param"]
            guard_call = [c for c in candidates if c.rule_type == "guard_call"]
            self.assertEqual(len(guard_param), 1)
            self.assertEqual(len(guard_call), 1)

    def test_detects_guard_field_and_fallout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/reference/ReferenceNode.java",
                (
                    "final class ReferenceNode {\n"
                    "    static short aShort9555;\n"
                    "    static void method3196(int i_3_) {\n"
                    "        if (i_3_ != 56) aShort9555 = (short) -74;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/ClientDebugCounters.java",
                (
                    "final class ClientDebugCounters {\n"
                    "    static void reset() {\n"
                    "        ReferenceNode.aShort9555 = 0;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src)
            roots = [c for c in candidates if c.rule_type == "guard_field" and c.member == "aShort9555"]
            self.assertEqual(len(roots), 1)
            fallout = [c for c in candidates if c.rule_type == "orphan_reset" and c.member == "aShort9555"]
            self.assertEqual(len(fallout), 1)
            self.assertEqual(fallout[0].dependency, roots[0].id)

    def test_rejects_real_param_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "cache/RealLogic.java",
                (
                    "final class RealLogic {\n"
                    "    static int evaluate(int i, int keep) {\n"
                    "        if (i != 0) return keep;\n"
                    "        return i + keep;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src)
            self.assertEqual([c for c in candidates if c.rule_type == "guard_param"], [])

    def test_detects_guard_dead_by_literal_callers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    static void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    static void clear() {}\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static void a() { SecondaryNodeIterator.next((byte) 79); }\n"
                    "    static void b() { SecondaryNodeIterator.next((byte) 60); }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="closed-world")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertIn("next[0] < 44", caller_dead[0].match)
            self.assertEqual(caller_dead[0].confidence, "medium")

    def test_promotes_private_guard_dead_by_callers_to_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    static void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    private static void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    static void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="closed-world")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertIn("closed-world semantic gate passed", caller_dead[0].notes)

    def test_caller_guard_auto_off_keeps_medium(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    static void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    private static void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    static void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="off")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "medium")

    def test_project_closed_world_promotes_non_private_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    static void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    static void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    static void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertIn("project-closed-world semantic gate passed", caller_dead[0].notes)

    def test_project_closed_world_promotes_local_non_private_instance_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")

    def test_project_closed_world_promotes_instance_guard_with_external_call_when_strict_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static void use(SecondaryNodeIterator iterator) {\n"
                    "        iterator.next((byte) 79);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [c for c in candidates if c.rule_type == "guard_dead_by_callers"]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_resolves_owner_for_casted_call_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNode.java",
                (
                    "class SecondaryNode {\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    SecondaryNode next(byte i) {\n"
                    "        if (i < 44) return null;\n"
                    "        return null;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/ChatChannel.java",
                (
                    "final class ChatChannel extends SecondaryNode {\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static void use(SecondaryNodeIterator iterator) {\n"
                    "        ChatChannel node = (ChatChannel) iterator.next((byte) 74);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers"
                and c.owner == "SecondaryNodeIterator"
                and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_keeps_instance_guard_medium_with_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/BaseIterator.java",
                (
                    "class BaseIterator {\n"
                    "    void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/DerivedIterator.java",
                (
                    "final class DerivedIterator extends BaseIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "BaseIterator" and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "medium")
            self.assertEqual(caller_dead[0].gate_reason, "instance_inheritance_boundary")

    def test_allowlist_promotes_blocked_inheritance_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                ".refactor-plan/guard_promotion_allowlist.csv",
                (
                    "file,owner,method,reason,notes\n"
                    "collections/list/BaseIterator.java,BaseIterator,next,instance_inheritance_boundary,test\n"
                ),
            )
            self._write(
                src,
                "collections/list/BaseIterator.java",
                (
                    "class BaseIterator {\n"
                    "    void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/DerivedIterator.java",
                (
                    "final class DerivedIterator extends BaseIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "BaseIterator" and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_denylist_overrides_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                ".refactor-plan/guard_promotion_allowlist.csv",
                (
                    "file,owner,method,reason,notes\n"
                    "collections/list/BaseIterator.java,BaseIterator,next,instance_inheritance_boundary,test\n"
                ),
            )
            self._write(
                src,
                ".refactor-plan/guard_promotion_denylist.csv",
                (
                    "file,owner,method,reason,notes\n"
                    "collections/list/BaseIterator.java,BaseIterator,next,instance_inheritance_boundary,test\n"
                ),
            )
            self._write(
                src,
                "collections/list/BaseIterator.java",
                (
                    "class BaseIterator {\n"
                    "    void tick() {\n"
                    "        next((byte) 79);\n"
                    "    }\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/DerivedIterator.java",
                (
                    "final class DerivedIterator extends BaseIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "BaseIterator" and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "medium")
            self.assertEqual(caller_dead[0].gate_reason, "instance_inheritance_boundary")

    def test_project_closed_world_resolves_chained_receiver_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/SecondaryNodeIterator.java",
                (
                    "final class SecondaryNodeIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/NodeIteratorHolder.java",
                (
                    "final class NodeIteratorHolder {\n"
                    "    SecondaryNodeIterator getIterator() {\n"
                    "        return null;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static NodeIteratorHolder holder;\n"
                    "    static void use() {\n"
                    "        holder.getIterator().next((byte) 79);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers"
                and c.owner == "SecondaryNodeIterator"
                and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_maps_subclass_receiver_to_declaring_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "collections/list/BaseIterator.java",
                (
                    "class BaseIterator {\n"
                    "    void next(byte i) {\n"
                    "        if (i < 44) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "collections/list/DerivedIterator.java",
                (
                    "final class DerivedIterator extends BaseIterator {\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static DerivedIterator iterator;\n"
                    "    static void use() {\n"
                    "        iterator.next((byte) 79);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "BaseIterator" and c.member == "next"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_uses_class_aliases_for_receiver_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                ".refactor-plan/classes.class_rename.csv",
                (
                    "old,new,phase,confidence,status,notes\n"
                    "Class348_Sub49,JagBuffer,core,high,approved,test\n"
                ),
            )
            self._write(
                src,
                "io/buffer/JagBuffer.java",
                (
                    "final class JagBuffer {\n"
                    "    void readByteSubtract(int i) {\n"
                    "        if (i != -27697) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static Class348_Sub49 buffer;\n"
                    "    static void use() {\n"
                    "        buffer.readByteSubtract(-27697);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "JagBuffer" and c.member == "readByteSubtract"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_uses_full_owner_chain_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "io/buffer/JagBuffer.java",
                (
                    "final class JagBuffer {\n"
                    "    void readIntMiddleEndian(byte i) {\n"
                    "        if (i != 82) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "net/packets/PacketBuffer.java",
                (
                    "final class PacketBuffer extends JagBuffer {\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "net/packets/InboundPacketBuffer.java",
                (
                    "abstract class InboundPacketBuffer {\n"
                    "    static PacketBuffer packetBuffer;\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static void use() {\n"
                    "        InboundPacketBuffer.packetBuffer.readIntMiddleEndian((byte) 82);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers"
                and c.owner == "JagBuffer"
                and c.member == "readIntMiddleEndian"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_uses_indexed_owner_chain_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "ui/input/KeyLogEntry.java",
                (
                    "final class KeyLogEntry {\n"
                    "    int getKeyCode(boolean bool) {\n"
                    "        if (bool != false) return -114;\n"
                    "        return 0;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Client.java",
                (
                    "final class Client {\n"
                    "    static KeyLogEntry[] entries;\n"
                    "    static void use() {\n"
                    "        entries[0].getKeyCode(false);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers" and c.owner == "KeyLogEntry" and c.member == "getKeyCode"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_uses_nested_args_owner_chain_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "config/IDKType.java",
                (
                    "final class IDKType {\n"
                    "    boolean areHeadModelsReady(byte i) {\n"
                    "        if (i <= 87) clear();\n"
                    "        return true;\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "config/IDKTypeList.java",
                (
                    "final class IDKTypeList {\n"
                    "    IDKType get(byte b, int id) {\n"
                    "        return null;\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "client/Caller.java",
                (
                    "final class Caller {\n"
                    "    static IDKTypeList list;\n"
                    "    static int value;\n"
                    "    static void use() {\n"
                    "        list.get((byte) 33, value & 0x3fffffff).areHeadModelsReady((byte) 110);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers"
                and c.owner == "IDKType"
                and c.member == "areHeadModelsReady"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")

    def test_project_closed_world_uses_casted_owner_chain_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            self._write(
                src,
                "graphics/native/ToolkitHeap.java",
                (
                    "class ToolkitHeap {\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "graphics/native/NativeHeapNode.java",
                (
                    "final class NativeHeapNode extends ToolkitHeap {\n"
                    "    void method3445(int i) {\n"
                    "        if (i != -9503) clear();\n"
                    "    }\n"
                    "    void clear() {\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write(
                src,
                "graphics/native/NativeToolkit.java",
                (
                    "final class NativeToolkit {\n"
                    "    static void use(ToolkitHeap class348) {\n"
                    "        ((NativeHeapNode) class348).method3445(-9503);\n"
                    "    }\n"
                    "}\n"
                ),
            )
            candidates = detect_unified_cleanup_candidates(src, caller_auto_mode="project-closed-world")
            caller_dead = [
                c
                for c in candidates
                if c.rule_type == "guard_dead_by_callers"
                and c.owner == "NativeHeapNode"
                and c.member == "method3445"
            ]
            self.assertEqual(len(caller_dead), 1)
            self.assertEqual(caller_dead[0].confidence, "high")
            self.assertEqual(caller_dead[0].gate_reason, "")


if __name__ == "__main__":
    unittest.main()
