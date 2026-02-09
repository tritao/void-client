#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.cleanup.candidate_detector import detect_unified_cleanup_candidates


@dataclass(frozen=True)
class GuardRegressionCase:
    name: str
    files: dict[str, str]
    target_file: str
    target_owner: str
    target_member: str
    expected_rules: tuple[str, ...]
    out_of_scope: bool = False


@dataclass(frozen=True)
class GuardRegressionReport:
    total_cases: int
    covered_cases: int
    out_of_scope_cases: int
    missed_cases: int
    missed_names: tuple[str, ...]
    out_of_scope_hit_names: tuple[str, ...]


CASES: tuple[GuardRegressionCase, ...] = (
    GuardRegressionCase(
        name="soft_factory_return_guard",
        files={
            "collections/reference/SoftReferenceNodeFactory.java": (
                "final class SoftReferenceNodeFactory {\n"
                "    static Object createSoftReferenceNode(int i, Object node) {\n"
                "        if (i != 3) return null;\n"
                "        return node;\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/reference/SoftReferenceNodeFactory.java",
        target_owner="SoftReferenceNodeFactory",
        target_member="createSoftReferenceNode",
        expected_rules=("guard_param",),
    ),
    GuardRegressionCase(
        name="shortnode_recursive_call_guard",
        files={
            "collections/value/ShortNode.java": (
                "final class ShortNode {\n"
                "    static void clearStaticState(int i) {\n"
                "        if (i != -4587) clearStaticState(-101);\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/value/ShortNode.java",
        target_owner="ShortNode",
        target_member="clearStaticState",
        expected_rules=("guard_param", "guard_call"),
    ),
    GuardRegressionCase(
        name="hard_reference_threshold_guard",
        files={
            "collections/reference/HardReferenceNode.java": (
                "final class HardReferenceNode {\n"
                "    static Object getValue(int i) {\n"
                "        if (i < 75) {}\n"
                "        return null;\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/reference/HardReferenceNode.java",
        target_owner="HardReferenceNode",
        target_member="getValue",
        expected_rules=("guard_param",),
    ),
    GuardRegressionCase(
        name="bytearray_bool_assignment_guard",
        files={
            "collections/value/ByteArraySecondaryNode.java": (
                "final class ByteArraySecondaryNode {\n"
                "    static Object recolorPalette;\n"
                "    static void clearStaticState(boolean bool) {\n"
                "        if (bool != true) recolorPalette = null;\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/value/ByteArraySecondaryNode.java",
        target_owner="ByteArraySecondaryNode",
        target_member="clearStaticState",
        expected_rules=("guard_param",),
    ),
    GuardRegressionCase(
        name="reference_guard_field_root",
        files={
            "collections/reference/ReferenceNode.java": (
                "final class ReferenceNode {\n"
                "    static short aShort9555;\n"
                "    static void method3196(int i_3_) {\n"
                "        if (i_3_ != 56) aShort9555 = (short) -74;\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/reference/ReferenceNode.java",
        target_owner="ReferenceNode",
        target_member="aShort9555",
        expected_rules=("guard_field",),
    ),
    GuardRegressionCase(
        name="reference_guard_field_orphan_reset",
        files={
            "collections/reference/ReferenceNode.java": (
                "final class ReferenceNode {\n"
                "    static short aShort9555;\n"
                "    static void method3196(int i_3_) {\n"
                "        if (i_3_ != 56) aShort9555 = (short) -74;\n"
                "    }\n"
                "}\n"
            ),
            "client/ClientDebugCounters.java": (
                "final class ClientDebugCounters {\n"
                "    static void reset() {\n"
                "        ReferenceNode.aShort9555 = 0;\n"
                "    }\n"
                "}\n"
            ),
        },
        target_file="client/ClientDebugCounters.java",
        target_owner="ReferenceNode",
        target_member="aShort9555",
        expected_rules=("orphan_reset",),
    ),
    GuardRegressionCase(
        name="reference_guard_field_orphan_increment",
        files={
            "collections/reference/ReferenceNode.java": (
                "final class ReferenceNode {\n"
                "    static int isSoftChecks;\n"
                "    static void method3196(int i_3_) {\n"
                "        if (i_3_ != 56) isSoftChecks = 1;\n"
                "    }\n"
                "}\n"
            ),
            "client/ClientDebugCounters.java": (
                "final class ClientDebugCounters {\n"
                "    static void bump() {\n"
                "        ReferenceNode.isSoftChecks++;\n"
                "    }\n"
                "}\n"
            ),
        },
        target_file="client/ClientDebugCounters.java",
        target_owner="ReferenceNode",
        target_member="isSoftChecks",
        expected_rules=("orphan_increment",),
    ),
    GuardRegressionCase(
        name="secondary_list_caller_literal_dead_guard",
        files={
            "collections/list/SecondaryNodeIterator.java": (
                "final class SecondaryNodeIterator {\n"
                "    static void next(byte i) {\n"
                "        if (i < 44) clear();\n"
                "    }\n"
                "    static void clear() {}\n"
                "}\n"
            ),
            "client/Caller.java": (
                "final class Caller {\n"
                "    static void a() { SecondaryNodeIterator.next((byte) 79); }\n"
                "    static void b() { SecondaryNodeIterator.next((byte) 60); }\n"
                "}\n"
            ),
        },
        target_file="collections/list/SecondaryNodeIterator.java",
        target_owner="SecondaryNodeIterator",
        target_member="next",
        expected_rules=("guard_dead_by_callers",),
    ),
    GuardRegressionCase(
        name="counter_increment_without_guard_field",
        files={
            "collections/reference/HardReferenceNode.java": (
                "final class HardReferenceNode {\n"
                "    static int isSoftChecks;\n"
                "    static void bump() {\n"
                "        isSoftChecks++;\n"
                "    }\n"
                "}\n"
            )
        },
        target_file="collections/reference/HardReferenceNode.java",
        target_owner="HardReferenceNode",
        target_member="isSoftChecks",
        expected_rules=(),
        out_of_scope=True,
    ),
    GuardRegressionCase(
        name="obsolete_wrapper_method_without_guard_param",
        files={
            "collections/hash/HashTable.java": (
                "final class HashTable {\n"
                "    static void clearStaticState() {\n"
                "        helper();\n"
                "    }\n"
                "    static void helper() {}\n"
                "}\n"
            )
        },
        target_file="collections/hash/HashTable.java",
        target_owner="HashTable",
        target_member="clearStaticState",
        expected_rules=(),
        out_of_scope=True,
    ),
)


def _write_fixture(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_guard_regression_report() -> GuardRegressionReport:
    covered_cases = 0
    out_of_scope_cases = 0
    missed_names: list[str] = []
    out_of_scope_hit_names: list[str] = []

    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            for rel, content in case.files.items():
                _write_fixture(src, rel, content)
            candidates = detect_unified_cleanup_candidates(src)

        scoped = [
            c
            for c in candidates
            if c.file == case.target_file and c.owner == case.target_owner and c.member == case.target_member
        ]
        hit_rules = {c.rule_type for c in scoped}

        if case.out_of_scope:
            out_of_scope_cases += 1
            if hit_rules:
                out_of_scope_hit_names.append(case.name)
            continue

        if set(case.expected_rules).issubset(hit_rules):
            covered_cases += 1
        else:
            missed_names.append(case.name)

    return GuardRegressionReport(
        total_cases=len(CASES),
        covered_cases=covered_cases,
        out_of_scope_cases=out_of_scope_cases,
        missed_cases=len(missed_names),
        missed_names=tuple(missed_names),
        out_of_scope_hit_names=tuple(out_of_scope_hit_names),
    )


def main() -> int:
    report = build_guard_regression_report()
    print(
        "manual_guard_coverage "
        f"total={report.total_cases} "
        f"covered={report.covered_cases} "
        f"out_of_scope={report.out_of_scope_cases} "
        f"missed={report.missed_cases} "
        f"out_of_scope_hits={len(report.out_of_scope_hit_names)}"
    )
    if report.missed_names:
        print("missed_cases:")
        for name in report.missed_names:
            print(name)
    if report.out_of_scope_hit_names:
        print("unexpected_out_of_scope_hits:")
        for name in report.out_of_scope_hit_names:
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
