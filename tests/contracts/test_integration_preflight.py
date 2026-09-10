from __future__ import annotations

from collections.abc import Mapping

from tools.validate_integration_inputs import build_preflight_report


HASH = "a" * 64
UPDATE = {"A-E01", "B-E04", "C-E07", "D-E02"}


def catalog() -> dict[str, object]:
    tasks: list[dict[str, str]] = []
    for member in "ABCD":
        tasks.extend(
            {"id": f"{member}-B{number:02d}", "kind": "base"}
            for number in range(1, 7)
        )
    tasks.extend({"id": task, "kind": "extension"} for task in sorted(UPDATE | {"A-E08"}))
    return {"tasks": tasks}


def groups() -> dict[str, object]:
    return {
        "groups": [
            {"id": "update", "mode": "atomic", "required_tasks": sorted(UPDATE)},
            {"id": "independent_a_e08", "mode": "per_task", "required_tasks": ["A-E08"]},
        ]
    }


def complete_manifests(extra: set[str] | None = None) -> dict[str, Mapping[str, object]]:
    extra = extra or set()
    result: dict[str, Mapping[str, object]] = {}
    for member in "ABCD":
        base = {f"{member}-B{number:02d}" for number in range(1, 7)}
        member_extra = {task for task in extra if task.startswith(f"{member}-")}
        completed = sorted(base | member_extra)
        result[member] = {
            "member": member,
            "completed_tasks": completed,
            "verified_tasks": completed,
            "contract_hash": HASH,
        }
    return result


def report(
    declared: set[str],
    completed_extensions: set[str] | None = None,
    mismatches: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return build_preflight_report(
        catalog=catalog(),
        feature_groups=groups(),
        manifests=complete_manifests(completed_extensions),
        declared_tasks=declared,
        expected_contract_hash=HASH,
        contract_mismatches=mismatches,
    )


def test_core_only_is_ready_without_extensions() -> None:
    result = report(set())
    assert result["status"] == "READY"
    assert result["ready_for_core"] is True
    assert result["enabled"] == []


def test_complete_update_group_is_enabled() -> None:
    result = report(UPDATE, UPDATE)
    assert result["enabled"] == [{"group": "update", "tasks": sorted(UPDATE)}]
    assert result["skipped"] == []


def test_partial_update_is_skipped_as_one_atomic_group() -> None:
    result = report({"A-E01"}, {"A-E01"})
    assert result["ready_for_core"] is True
    skipped = result["skipped"]
    assert isinstance(skipped, list)
    assert skipped[0]["group"] == "update"
    assert skipped[0]["reason"] == "incomplete_atomic_group"
    assert set(skipped[0]["missing_tasks"]) == UPDATE - {"A-E01"}


def test_completed_but_undeclared_extension_is_ignored() -> None:
    result = report(set(), {"A-E08"})
    assert result["enabled"] == []
    assert result["ignored_unlisted_extensions"] == ["A-E08"]


def test_contract_mismatch_blocks_core_and_extension_enablement() -> None:
    mismatch = [{"member": "C", "expected": HASH, "actual": "b" * 64, "source": "contract_tree"}]
    result = report(UPDATE, UPDATE, mismatch)
    assert result["status"] == "BLOCKED_CORE"
    assert result["ready_for_core"] is False
    assert result["enabled"] == []
    assert result["skipped"][0]["reason"] == "contract_mismatch"


def test_failed_extension_evidence_skips_only_its_group() -> None:
    manifests = complete_manifests(UPDATE)
    member_d = dict(manifests["D"])
    member_d["verified_tasks"] = [
        task for task in member_d["verified_tasks"] if task != "D-E02"
    ]
    manifests["D"] = member_d

    result = build_preflight_report(
        catalog=catalog(),
        feature_groups=groups(),
        manifests=manifests,
        declared_tasks=UPDATE,
        expected_contract_hash=HASH,
    )

    assert result["ready_for_core"] is True
    assert result["status"] == "READY_WITH_SKIPPED_EXTENSIONS"
    assert result["enabled"] == []
    assert result["missing"]["extension_test_evidence"] == ["D-E02"]
    assert result["skipped"] == [
        {
            "group": "update",
            "tasks": sorted(UPDATE),
            "missing_tasks": ["D-E02"],
            "reason": "incomplete_atomic_group",
        }
    ]


def test_verified_independent_extension_can_enable_alone() -> None:
    result = report({"A-E08"}, {"A-E08"})
    assert result["ready_for_core"] is True
    assert result["enabled"] == [{"group": "independent_a_e08", "tasks": ["A-E08"]}]
    assert result["skipped"] == []
