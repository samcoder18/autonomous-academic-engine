from __future__ import annotations

import unittest

from academic_engine.action_specs import AllowedWriteScope, ExecutionContract, RepairPolicy
from academic_engine.repair_kernel import (
    Blocker,
    build_repair_decision,
    build_repair_plan,
    run_bounded_repair_loop,
)


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

    def test_citation_blocker_routes_to_citation_checker(self) -> None:
        blocker = Blocker(category="citation", code="missing-footnote", message="Missing footnote.")

        plan = build_repair_plan(
            contract=_contract(scopes=("draft", "checklist", "review")),
            blockers=[blocker],
            repair_iteration=1,
        )

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.assigned_role, "academic-citation-checker")
        self.assertEqual(step.rerun_gate, "citation-check")
        self.assertEqual([scope.name for scope in step.allowed_write_scopes], ["draft", "checklist", "review"])

    def test_logic_blocker_routes_to_counterargument_critic(self) -> None:
        blocker = Blocker(category="logic", code="overclaim", message="Conclusion overstates support.")

        plan = build_repair_plan(
            contract=_contract(scopes=("draft", "final-markdown", "review")),
            blockers=[blocker],
            repair_iteration=1,
        )

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.assigned_role, "academic-counterargument-critic")
        self.assertEqual(step.rerun_gate, "argument-review")

    def test_standards_blocker_routes_to_finalizer_when_not_safe_only(self) -> None:
        blocker = Blocker(
            category="standards-consistency",
            code="missing-format-profile",
            message="Formatting profile is incomplete.",
        )

        plan = build_repair_plan(
            contract=_contract(scopes=("checklist", "final-markdown", "docx")),
            blockers=[blocker],
            repair_iteration=1,
        )

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.assigned_role, "academic-finalizer")
        self.assertEqual(step.rerun_gate, "standards-check")

    def test_safe_only_filters_style_and_standards_steps(self) -> None:
        blockers = [
            Blocker(category="style", code="flat-prose", message="Style needs polish."),
            Blocker(category="standards", code="formatting-gap", message="Formatting profile is incomplete."),
        ]

        plan = build_repair_plan(
            contract=_contract(scopes=("draft", "checklist"), safe_only=True),
            blockers=blockers,
            repair_iteration=1,
        )

        self.assertEqual(plan.steps, ())
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.focus_areas, ())

    def test_unrepairable_blocker_does_not_create_step(self) -> None:
        blocker = Blocker(
            category="citation",
            code="missing-source",
            message="Citation source is unavailable.",
            repairable=False,
        )

        plan = build_repair_plan(
            contract=_contract(scopes=("draft", "checklist")),
            blockers=[blocker],
            repair_iteration=1,
        )

        self.assertEqual(plan.steps, ())
        self.assertEqual(plan.blockers, ())

    def test_decision_stops_when_no_allowed_write_scope_matches(self) -> None:
        blocker = Blocker(
            category="primary-support",
            code="unsupported-claim",
            message="Strong claim is missing primary support.",
        )

        decision = build_repair_decision(
            contract=_contract(scopes=("article-root",)),
            blockers=[blocker],
            repair_iteration=0,
        )

        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.reason, "no-safe-repair-steps")
        self.assertEqual(decision.terminal_reason, "blocked-primary-support")
        self.assertEqual(decision.blocker_count, 1)

    def test_decision_repairs_when_at_least_one_safe_step_exists(self) -> None:
        blocker = Blocker(
            category="citation",
            code="missing-footnote",
            message="Missing footnote.",
        )

        decision = build_repair_decision(
            contract=_contract(scopes=("draft", "checklist")),
            blockers=[blocker],
            repair_iteration=0,
        )

        self.assertEqual(decision.action, "repair")
        self.assertEqual(decision.reason, "repairable-blockers-available")
        self.assertEqual(decision.repair_iteration, 1)
        self.assertEqual(decision.blocker_count, 1)

    def test_decision_still_stops_at_max_iterations(self) -> None:
        blocker = Blocker(category="citation", code="persistent-gap", message="Citation gap remains.")

        decision = build_repair_decision(
            contract=_contract(scopes=("draft", "checklist"), max_iterations=2),
            blockers=[blocker],
            repair_iteration=2,
        )

        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.reason, "repair-limit-reached")
        self.assertEqual(decision.terminal_reason, "max-repair-iterations")

    def test_thesis_citation_blocker_routes_to_thesis_citation_checker(self) -> None:
        blocker = Blocker(category="citation", code="missing-footnote", message="Missing footnote.")

        plan = build_repair_plan(
            contract=_contract(lane="thesis", action="verify", scopes=("canonical-target", "sync", "draft")),
            blockers=[blocker],
            repair_iteration=1,
        )

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].assigned_role, "thesis-citation-checker")
        self.assertEqual(plan.steps[0].rerun_gate, "citation-check")

    def test_bounded_repair_loop_passes_steps_to_repair_callback(self) -> None:
        blocker = Blocker(
            category="primary-support",
            code="unsupported-claim",
            message="Strong claim is missing primary support.",
        )
        seen_roles: list[str] = []

        def repair_fn(plan):
            seen_roles.extend(step.assigned_role for step in plan.steps)
            return {"patched": True}

        def evaluate_fn(plan, repair_result):
            self.assertEqual([step.rerun_gate for step in plan.steps], ["source-verification"])
            self.assertTrue(repair_result["patched"])
            return []

        outcome = run_bounded_repair_loop(
            contract=_contract(scopes=("evidence-pack", "draft")),
            initial_blockers=[blocker],
            repair_fn=repair_fn,
            evaluate_fn=evaluate_fn,
        )

        self.assertEqual(seen_roles, ["academic-source-verifier"])
        self.assertEqual(outcome.terminal_reason, "ready")
        self.assertEqual(outcome.repair_iteration, 1)
        self.assertEqual(outcome.remaining_blockers, ())
        self.assertEqual(len(outcome.plans), 1)
        self.assertEqual(outcome.plans[0].steps[0].assigned_role, "academic-source-verifier")


if __name__ == "__main__":
    unittest.main()
