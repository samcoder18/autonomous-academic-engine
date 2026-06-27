from __future__ import annotations

import unittest

from academic_engine.action_specs import AllowedWriteScope, ExecutionContract, RepairPolicy
from academic_engine.repair_kernel import Blocker, build_repair_plan


def _scope(name: str) -> AllowedWriteScope:
    return AllowedWriteScope(
        name=name,
        path=f"works/demo/{name}.md",
        description=f"{name} scope.",
    )


def _contract(
    *,
    lane: str = "article",
    action: str = "repair",
    scopes: tuple[str, ...] = ("article-root", "evidence-pack", "draft", "checklist"),
    eligible: bool = True,
    max_iterations: int = 2,
    safe_only: bool = False,
) -> ExecutionContract:
    return ExecutionContract(
        lane=lane,
        action=action,
        title="Repair",
        summary="Repair test contract.",
        target_kind="test target",
        target_validation="validated",
        prompt_rules=(),
        deliverables=(),
        required_context=(),
        allowed_write_scopes=tuple(_scope(name) for name in scopes),
        required_outputs=(),
        required_checkpoints=(),
        terminal_statuses=("submission-ready", "strong-draft-with-blockers"),
        quality_gates=(),
        repair_policy=RepairPolicy(
            eligible=eligible,
            max_iterations=max_iterations,
            safe_only=safe_only,
            triggers=("existing blockers",),
            terminal_reasons=("ready", "blocked-primary-support", "max-repair-iterations"),
        ),
        transitions=(),
        metadata=(),
    )


class RepairKernelStepRoutingTests(unittest.TestCase):
    def test_primary_support_blocker_builds_source_verifier_step(self) -> None:
        blocker = Blocker(
            category="primary-support",
            code="unsupported-claim",
            message="Strong claim is missing primary support.",
            repairable=True,
            blocks_statuses=("submission-ready",),
        )

        plan = build_repair_plan(contract=_contract(), blockers=[blocker], repair_iteration=1)

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.blocker, blocker)
        self.assertEqual(step.assigned_role, "academic-source-verifier")
        self.assertEqual(step.rerun_gate, "source-verification")
        self.assertEqual(step.stop_condition, "blocker-cleared-or-reroute-required")
        self.assertTrue(step.safe)
        self.assertIn("verifier", step.reason)
        self.assertEqual([scope.name for scope in step.allowed_write_scopes], ["evidence-pack"])

        payload = plan.to_dict()
        self.assertEqual(payload["steps"][0]["assigned_role"], "academic-source-verifier")
        self.assertEqual(payload["steps"][0]["rerun_gate"], "source-verification")
        self.assertEqual(payload["steps"][0]["allowed_write_scopes"][0]["name"], "evidence-pack")
        self.assertEqual(payload["blockers"][0]["code"], "unsupported-claim")
        self.assertEqual(payload["focus_areas"], ["primary-support"])


if __name__ == "__main__":
    unittest.main()
