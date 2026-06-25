# Repair Loop Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured repair steps so each repairable blocker can be routed to a role, allowed write scopes, rerun gate, and stop condition.

**Architecture:** Extend the existing `academic_engine.repair_kernel` dataclasses instead of adding a parallel planner. `RepairPlan` keeps its existing fields for compatibility and gains a `steps` tuple; `build_repair_decision()` starts repair only when a safe step exists.

**Tech Stack:** Python dataclasses, stdlib `unittest`, existing `academic_engine.action_specs` contract dataclasses, existing repair kernel.

---

## File Structure

- Create `tests/test_repair_kernel.py`: focused unit tests for repair-step routing, safe filtering, stop decisions, serialization, and bounded-loop compatibility.
- Modify `academic_engine/repair_kernel.py`: add `RepairStep`, private route helpers, write-scope matching, `steps` serialization, and decision gating on safe steps.
- No changes to `WorkflowOrchestrator`, `EngineService`, CLI command parsing, runtime storage layout, or external dependencies.

## Implementation Tasks

### Task 1: Primary-Support Repair Step and Serialization

**Files:**
- Create: `tests/test_repair_kernel.py`
- Modify: `academic_engine/repair_kernel.py`

- [ ] **Step 1: Write the failing primary-support routing test**

Create `tests/test_repair_kernel.py` with this content:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests.test_primary_support_blocker_builds_source_verifier_step -q
```

Expected: FAIL with `AttributeError: 'RepairPlan' object has no attribute 'steps'`.

- [ ] **Step 3: Add the minimal `RepairStep` implementation**

In `academic_engine/repair_kernel.py`, change the import and add `RepairStep` after `RepairDecision`:

```python
from .action_specs import AllowedWriteScope, ExecutionContract
```

```python
@dataclass(frozen=True)
class RepairStep:
    blocker: Blocker
    assigned_role: str
    allowed_write_scopes: tuple[AllowedWriteScope, ...]
    rerun_gate: str
    stop_condition: str
    safe: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker": self.blocker.to_dict(),
            "assigned_role": self.assigned_role,
            "allowed_write_scopes": [item.to_dict() for item in self.allowed_write_scopes],
            "rerun_gate": self.rerun_gate,
            "stop_condition": self.stop_condition,
            "safe": self.safe,
            "reason": self.reason,
        }
```

Update `RepairPlan`:

```python
@dataclass(frozen=True)
class RepairPlan:
    lane: str
    action: str
    repair_iteration: int
    blockers: tuple[Blocker, ...]
    focus_areas: tuple[str, ...]
    safe_only: bool
    max_iterations: int
    steps: tuple[RepairStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "action": self.action,
            "repair_iteration": self.repair_iteration,
            "blockers": [item.to_dict() for item in self.blockers],
            "focus_areas": list(self.focus_areas),
            "safe_only": self.safe_only,
            "max_iterations": self.max_iterations,
            "steps": [item.to_dict() for item in self.steps],
        }
```

Add these helpers above `build_repair_plan()`:

```python
SOURCE_REPAIR_CATEGORIES = {"dynamic-material", "primary-support", "source", "verification"}
SOURCE_SCOPE_NAMES = ("evidence", "ledger", "source", "sync", "requested-target", "canonical-target", "target")


def _role_for_source(lane: str) -> str:
    return "thesis-source-verifier" if lane == "thesis" else "academic-source-verifier"


def _build_repair_steps(*, contract: ExecutionContract, blockers: tuple[Blocker, ...]) -> tuple[RepairStep, ...]:
    steps: list[RepairStep] = []
    for blocker in blockers:
        if blocker.category not in SOURCE_REPAIR_CATEGORIES:
            continue
        scopes = _matching_write_scopes(contract.allowed_write_scopes, SOURCE_SCOPE_NAMES)
        if not scopes:
            continue
        steps.append(
            RepairStep(
                blocker=blocker,
                assigned_role=_role_for_source(contract.lane),
                allowed_write_scopes=scopes,
                rerun_gate="source-verification",
                stop_condition="blocker-cleared-or-reroute-required",
                safe=True,
                reason="Primary support blockers require verifier evidence before drafting or finalization.",
            )
        )
    return tuple(steps)


def _matching_write_scopes(
    scopes: tuple[AllowedWriteScope, ...],
    preferred_names: tuple[str, ...],
) -> tuple[AllowedWriteScope, ...]:
    matches: list[AllowedWriteScope] = []
    for scope in scopes:
        name = scope.name.lower()
        if any(preferred in name for preferred in preferred_names):
            matches.append(scope)
    return tuple(matches)
```

Update `build_repair_plan()` to include steps:

```python
    steps = _build_repair_steps(contract=contract, blockers=repairable)
    return RepairPlan(
        lane=contract.lane,
        action=contract.action,
        repair_iteration=repair_iteration,
        blockers=repairable,
        focus_areas=focus_areas,
        safe_only=contract.repair_policy.safe_only,
        max_iterations=contract.repair_policy.max_iterations,
        steps=steps,
    )
```

- [ ] **Step 4: Run the primary-support test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests.test_primary_support_blocker_builds_source_verifier_step -q
```

Expected: `OK`.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add academic_engine/repair_kernel.py tests/test_repair_kernel.py
git commit -m "feat: add repair step routing"
```

### Task 2: Category Routing Matrix

**Files:**
- Modify: `tests/test_repair_kernel.py`
- Modify: `academic_engine/repair_kernel.py`

- [ ] **Step 1: Add failing routing matrix tests**

Append these methods inside `RepairKernelStepRoutingTests`:

```python
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
```

- [ ] **Step 2: Run the new routing tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests -q
```

Expected: FAIL because citation, logic, and standards blockers produce no `steps`.

- [ ] **Step 3: Implement the routing matrix**

In `academic_engine/repair_kernel.py`, replace the Task 1 helper constants and helpers with this fuller routing code:

```python
SOURCE_REPAIR_CATEGORIES = {"dynamic-material", "primary-support", "source", "verification"}
CITATION_REPAIR_CATEGORIES = {"citation"}
LOGIC_REPAIR_CATEGORIES = {"logic", "review"}
STANDARDS_REPAIR_CATEGORIES = {"standards", "standards-consistency"}
STYLE_REPAIR_CATEGORIES = {"style"}

SOURCE_SCOPE_NAMES = ("evidence", "ledger", "source", "sync", "requested-target", "canonical-target", "target")
CITATION_SCOPE_NAMES = ("draft", "final-markdown", "checklist", "review", "ledger", "requested-target", "target")
LOGIC_SCOPE_NAMES = ("draft", "final-markdown", "review", "checklist", "canonical-target", "requested-target", "target")
STANDARDS_SCOPE_NAMES = ("checklist", "final-markdown", "docx", "standards", "requested-target", "target")
STYLE_SCOPE_NAMES = ("draft", "final-markdown", "canonical-target", "full-draft", "requested-target", "target")


@dataclass(frozen=True)
class _RepairRoute:
    assigned_role: str
    rerun_gate: str
    preferred_scope_names: tuple[str, ...]
    stop_condition: str
    reason: str
    safe: bool = True


def _route_for_blocker(*, lane: str, category: str) -> _RepairRoute | None:
    if category in RUNTIME_BLOCKER_CATEGORIES:
        return None
    if category in SOURCE_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-source-verifier" if lane == "thesis" else "academic-source-verifier",
            rerun_gate="source-verification",
            preferred_scope_names=SOURCE_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Primary support blockers require verifier evidence before drafting or finalization.",
        )
    if category in CITATION_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-citation-checker" if lane == "thesis" else "academic-citation-checker",
            rerun_gate="citation-check",
            preferred_scope_names=CITATION_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Citation blockers require a citation check before evaluator promotion.",
        )
    if category in LOGIC_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-argument-critic" if lane == "thesis" else "academic-counterargument-critic",
            rerun_gate="argument-review",
            preferred_scope_names=LOGIC_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Logic and review blockers require a critic pass before evaluator promotion.",
        )
    if category in STANDARDS_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-submission-evaluator" if lane == "thesis" else "academic-finalizer",
            rerun_gate="standards-check",
            preferred_scope_names=STANDARDS_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-downgrade-preserved",
            reason="Standards blockers must remain visible until the checklist or gate confirms resolution.",
        )
    if category in STYLE_REPAIR_CATEGORIES:
        return _RepairRoute(
            assigned_role="thesis-style-editor" if lane == "thesis" else "academic-draft-writer",
            rerun_gate="style-review",
            preferred_scope_names=STYLE_SCOPE_NAMES,
            stop_condition="blocker-cleared-or-reroute-required",
            reason="Style blockers may be repaired only when the contract allows non-safe repair.",
            safe=False,
        )
    return None
```

Update `_build_repair_steps()` to use `_route_for_blocker()`:

```python
def _build_repair_steps(*, contract: ExecutionContract, blockers: tuple[Blocker, ...]) -> tuple[RepairStep, ...]:
    steps: list[RepairStep] = []
    for blocker in blockers:
        route = _route_for_blocker(lane=contract.lane, category=blocker.category)
        if route is None:
            continue
        scopes = _matching_write_scopes(contract.allowed_write_scopes, route.preferred_scope_names)
        if not scopes:
            continue
        steps.append(
            RepairStep(
                blocker=blocker,
                assigned_role=route.assigned_role,
                allowed_write_scopes=scopes,
                rerun_gate=route.rerun_gate,
                stop_condition=route.stop_condition,
                safe=route.safe,
                reason=route.reason,
            )
        )
    return tuple(steps)
```

Delete the Task 1 `_role_for_source()` helper because `_route_for_blocker()` replaces it.

- [ ] **Step 4: Run routing tests to verify they pass**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests -q
```

Expected: `OK`.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add academic_engine/repair_kernel.py tests/test_repair_kernel.py
git commit -m "feat: route repair blockers by category"
```

### Task 3: Decision Gating on Safe Steps

**Files:**
- Modify: `tests/test_repair_kernel.py`
- Modify: `academic_engine/repair_kernel.py`

- [ ] **Step 1: Add failing stop-decision tests**

Update the repair-kernel import in `tests/test_repair_kernel.py`:

```python
from academic_engine.repair_kernel import Blocker, build_repair_decision, build_repair_plan
```

Append these methods inside `RepairKernelStepRoutingTests`:

```python
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
```

- [ ] **Step 2: Run the stop-decision tests to verify one fails**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests.test_decision_stops_when_no_allowed_write_scope_matches tests.test_repair_kernel.RepairKernelStepRoutingTests.test_decision_repairs_when_at_least_one_safe_step_exists tests.test_repair_kernel.RepairKernelStepRoutingTests.test_decision_still_stops_at_max_iterations -q
```

Expected: FAIL in `test_decision_stops_when_no_allowed_write_scope_matches` because the current decision checks `plan.blockers` rather than `plan.steps`.

- [ ] **Step 3: Gate repair decisions on `plan.steps`**

In `build_repair_decision()` in `academic_engine/repair_kernel.py`, replace:

```python
    if not plan.blockers:
        return RepairDecision(
            action="stop",
            reason="no-safe-repairs",
            repair_iteration=repair_iteration,
            terminal_reason=determine_terminal_reason(normalized),
            blocker_count=len(normalized),
        )
```

with:

```python
    if not plan.steps:
        return RepairDecision(
            action="stop",
            reason="no-safe-repair-steps",
            repair_iteration=repair_iteration,
            terminal_reason=determine_terminal_reason(normalized),
            blocker_count=len(normalized),
        )
```

Keep the repair branch as:

```python
    return RepairDecision(
        action="repair",
        reason="repairable-blockers-available",
        repair_iteration=plan.repair_iteration,
        blocker_count=len(plan.blockers),
    )
```

- [ ] **Step 4: Run all repair-kernel tests**

Run:

```bash
python3 -m unittest tests.test_repair_kernel -q
```

Expected: `OK`.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add academic_engine/repair_kernel.py tests/test_repair_kernel.py
git commit -m "fix: require safe repair steps for repair decisions"
```

### Task 4: Bounded Loop Compatibility and Thesis Role Routing

**Files:**
- Modify: `tests/test_repair_kernel.py`
- Modify: `academic_engine/repair_kernel.py` only if the tests expose a gap.

- [ ] **Step 1: Add bounded-loop and thesis routing tests**

Update the import in `tests/test_repair_kernel.py`:

```python
from academic_engine.repair_kernel import (
    Blocker,
    build_repair_decision,
    build_repair_plan,
    run_bounded_repair_loop,
)
```

Append these methods inside `RepairKernelStepRoutingTests`:

```python
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
```

- [ ] **Step 2: Run the bounded-loop tests**

Run:

```bash
python3 -m unittest tests.test_repair_kernel.RepairKernelStepRoutingTests.test_thesis_citation_blocker_routes_to_thesis_citation_checker tests.test_repair_kernel.RepairKernelStepRoutingTests.test_bounded_repair_loop_passes_steps_to_repair_callback -q
```

Expected after Tasks 1-3: `OK`. If this fails, inspect the failure and make the smallest correction in `academic_engine/repair_kernel.py`.

- [ ] **Step 3: Run existing repair integration regression tests**

Run:

```bash
python3 -m unittest tests.test_academic_engine.RepairKernelTests.test_bounded_article_repair_loop_recovers_after_one_iteration tests.test_academic_engine.RepairKernelTests.test_bounded_article_repair_loop_stops_at_max_iterations tests.test_academic_engine.RepairKernelTests.test_thesis_safe_repair_plan_filters_broad_style_blockers -q
```

Expected: `OK`.

- [ ] **Step 4: Run all focused repair tests**

Run:

```bash
python3 -m unittest tests.test_repair_kernel tests.test_academic_engine -q
```

Expected: `OK`.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add academic_engine/repair_kernel.py tests/test_repair_kernel.py
git commit -m "test: cover repair loop step compatibility"
```

### Task 5: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run unit tests for repair and runtime surfaces**

Run:

```bash
python3 -m unittest tests.test_repair_kernel tests.test_academic_engine tests.test_work_cli_runtime -q
```

Expected: `OK`.

- [ ] **Step 2: Run the broader unit suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: `OK`.

- [ ] **Step 3: Run lint**

Run:

```bash
python3 -m ruff check academic_engine tests
```

Expected: all checks pass.

- [ ] **Step 4: Run format check**

Run:

```bash
python3 -m ruff format --check academic_engine tests
```

Expected: all files already formatted.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: working tree clean after Task 4 commit, with recent commits for repair-step routing, category routing, decision gating, and compatibility tests.

## Self-Review

Spec coverage:

- Per-blocker role routing is covered by Tasks 1, 2, and 4.
- Allowed write scopes are covered by Tasks 1, 2, and 3.
- Rerun gates are covered by Tasks 1, 2, and 4.
- Stop conditions are covered by Task 3 and existing max-iteration regression in Task 4.
- Compatibility with old `RepairPlan` fields is covered by Task 1 serialization assertions and Task 4 existing regression tests.
- No new dependency is introduced; all code uses dataclasses and existing contract dataclasses.

Type consistency:

- `RepairStep.allowed_write_scopes` uses existing `AllowedWriteScope`.
- `RepairPlan.steps` is a tuple of `RepairStep`.
- `RepairStep.to_dict()` serializes nested `Blocker` and `AllowedWriteScope` through existing `to_dict()` methods.
- `build_repair_decision()` preserves current `RepairDecision` fields and only changes the unsafe/no-scope stop reason to `no-safe-repair-steps`.

Execution boundary:

- The plan does not change CLI command parsing, daemon runtime layout, `WorkflowOrchestrator`, `EngineService`, or external source connector behavior.
- The implementation stays dependency-free and file-first.
