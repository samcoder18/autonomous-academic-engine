# Repair Loop Plan Design

## Goal

Improve the bounded repair loop so blockers become a safe, machine-readable repair plan rather than a loose instruction to "fix blockers".

For each repairable blocker, the engine should be able to state:

- which blocker is being addressed;
- which role should address it;
- which files or write scopes may be changed;
- which gate must be rerun after the repair;
- when the loop must stop instead of cycling.

## Context

The repo already has repair machinery:

- `academic_engine.repair_kernel` defines `Blocker`, `RepairDecision`, `RepairPlan`, and `run_bounded_repair_loop`.
- `academic_engine.thesis_repair_planner` provides a thesis-specific safe repair plan for section-level thesis work.
- `academic_engine.action_specs` defines lane/action contracts, allowed write scopes, quality gates, and repair policy.
- The daemon already blocks autonomous repair unless repair planner metadata exists.

The missing piece is a richer common plan. The current common `RepairPlan` keeps blockers and focus areas, but it does not explicitly route each blocker to a role, allowed files, rerun gate, or stop condition. That means prompt text can say "repair safely", but the runtime and future UI cannot inspect the plan with enough precision.

This change should stay inside the existing file-first engine boundary. It should not introduce HTTP, a database, a new dependency, or a rewrite of `WorkflowOrchestrator`.

## Recommended Approach

Extend `academic_engine.repair_kernel.RepairPlan` with structured `RepairStep` items.

`RepairStep` should be a dependency-free dataclass serialized through `to_dict()`. It should be built from the existing `ExecutionContract`, `Blocker` list, repair policy, and allowed write scopes.

The existing `RepairPlan` fields remain for compatibility:

- `lane`
- `action`
- `repair_iteration`
- `blockers`
- `focus_areas`
- `safe_only`
- `max_iterations`

The new `steps` field becomes the authoritative machine-readable repair route. Old consumers can keep reading `blockers` and `focus_areas`; new consumers can use `steps`.

## Alternatives Considered

### Separate Article Planner

Create `academic_engine.article_repair_planner` parallel to `thesis_repair_planner`.

This would be clean for article-only behavior, but it would duplicate blocker-to-role routing and leave the common repair kernel too weak for daemon and future UI use.

### Prompt-Only Repair Rules

Update role docs and launcher prompts with stronger repair instructions.

This is fast, but insufficient. Prompt-only rules are not inspectable by daemon policy, CLI status, tests, or future FastAPI/Next.js control plane. The system needs a structured plan, not just better prose.

### Recommended Kernel Extension

Keep the logic in `repair_kernel`, with optional lane-specific refinement later.

This matches the existing architecture: contracts remain in `action_specs`, runtime decisions remain in the repair kernel, and lane-specific planners can consume or refine the common plan without replacing it.

## Repair Step Shape

Each `RepairStep` should include:

```json
{
  "blocker": {
    "category": "primary-support",
    "code": "unsupported-claim",
    "message": "Strong claim lacks primary support.",
    "repairable": true,
    "blocks_statuses": ["submission-ready"]
  },
  "assigned_role": "academic-source-verifier",
  "allowed_write_scopes": [
    {
      "name": "evidence-pack",
      "path": "works/demo/articles/evidence/demo.md",
      "description": "Managed evidence pack."
    }
  ],
  "rerun_gate": "source-verification",
  "stop_condition": "blocker-cleared-or-reroute-required",
  "safe": true,
  "reason": "Primary support blockers require verifier evidence before drafting or finalization."
}
```

The exact role id is a routing hint, not a claim that the role already ran. The gate is the required next validation layer after a repair attempt.

## Routing Rules

Start with deterministic category routing:

- `primary-support`, `verification`, `dynamic-material`, `source` route to `academic-source-verifier` or thesis verifier behavior, with rerun gate `source-verification`.
- `citation` routes to `academic-citation-checker`, with rerun gate `citation-check`.
- `logic` and `review` route to `academic-counterargument-critic` or thesis argument critic behavior, with rerun gate `argument-review`.
- `standards` and `standards-consistency` route to `academic-finalizer`, with rerun gate `standards-check`.
- `style` is repairable only when the contract is not `safe_only`; it routes to style/editor behavior, with rerun gate `style-review`.
- `runtime`, `codex`, `external`, `artifact`, and `process` do not produce normal repair steps by default. They produce stop or reroute metadata.
- Any blocker with `repairable=false` does not produce a repair step.

When `contract.repair_policy.safe_only` is true, only categories already allowed by `SAFE_REPAIR_CATEGORIES` can produce steps.

## Allowed Write Scope Selection

The plan must not invent file authority. It should select write scopes from `ExecutionContract.allowed_write_scopes`.

Initial scope matching:

- verifier/source blockers prefer evidence, ledger, source, sync, and requested target scopes;
- citation blockers prefer draft, final markdown, checklist, review, ledger, and requested target scopes;
- logic/review blockers prefer draft, final markdown, review, checklist, canonical target, and requested target scopes;
- standards blockers prefer checklist, final markdown, docx, standards-related output scopes, and requested target scopes;
- style blockers prefer draft, final markdown, canonical target, full draft, and requested target scopes.

If no allowed write scope matches a repairable blocker, the blocker remains in `RepairPlan.blockers`, but no step is created for it. `build_repair_decision()` should then stop with a clear terminal reason when there are blockers but no safe steps.

## Stop Conditions

The loop must stop when:

- there are no blockers;
- the action is not repair-eligible;
- `repair_iteration >= contract.repair_policy.max_iterations`;
- blockers exist but no safe repair steps can be created;
- blockers are unrepairable;
- the next required action is a reroute rather than a bounded repair;
- the same blocker survives the maximum configured iterations.

Existing terminal reasons should remain stable where possible:

- `ready`
- `blocked-runtime`
- `blocked-standards`
- `blocked-primary-support`
- `ready-with-caveats`
- `max-repair-iterations`

The decision reason can become more specific, for example `no-safe-repair-steps`, while preserving older reasons only where tests or runtime consumers already depend on them.

## Data Flow

1. A role, evaluator, machine gate, or runtime check emits structured blockers.
2. The caller resolves the `ExecutionContract` for the lane/action.
3. `build_repair_plan()` normalizes blockers and builds routeable `RepairStep` items.
4. `build_repair_decision()` returns `repair` only when at least one safe step exists and the iteration limit has not been reached.
5. `run_bounded_repair_loop()` passes the plan to `repair_fn`.
6. The repair role changes only files allowed by the step and contract.
7. The required rerun gate evaluates fresh artifacts.
8. If blockers remain, the next decision either plans another bounded iteration or stops with a terminal reason.

## CLI, Runtime, and Future UI

The first implementation should keep existing text output stable unless a focused status enhancement is needed.

The JSON/runtime shape should be ready for richer surfaces:

- daemon policy can require `repair_decision.action == "repair"` and at least one `repair_plan.steps` item before autonomous repair;
- CLI `work-status --json` can expose the latest repair step list;
- future UI can render a per-blocker repair board without parsing prompts.

Prompt generation can later include a compact "Repair plan" section that lists assigned role, allowed paths, rerun gate, and stop condition.

## Testing

Use TDD before production changes.

Focused tests should cover:

- primary-support blocker produces a verifier step with source-verification gate;
- citation blocker produces a citation-checker step with citation-check gate;
- logic/review blocker produces critic/review routing;
- standards blocker routes to finalizer/checklist behavior and preserves blockers;
- `safe_only` filters style or other unsafe categories;
- unrepairable blockers produce no steps;
- repairable blockers with no matching allowed write scope produce a stop decision;
- max-iteration behavior remains unchanged;
- `run_bounded_repair_loop()` remains compatible with existing repair callbacks;
- `RepairPlan.to_dict()` preserves old fields and includes `steps`.

The likely primary test file is `tests/test_academic_engine.py`, where repair kernel and contract integration are already tested. If the test block grows too large, split focused repair-kernel coverage into `tests/test_repair_kernel.py`.

## Rollout

Implement in narrow slices:

1. Add failing tests for `RepairStep` routing and serialization.
2. Add `RepairStep` and route-building helpers to `repair_kernel`.
3. Update `RepairPlan.to_dict()` and `build_repair_plan()`.
4. Update `build_repair_decision()` to require at least one safe step for a repair decision.
5. Add a focused prompt/status enhancement only if needed to expose the plan.
6. Run focused repair tests, then the broader relevant regression pack.

No new runtime dependency is introduced.

## Acceptance Criteria

- Each repairable blocker can become an explicit repair step with role, allowed write scopes, rerun gate, and stop condition.
- Unsafe, unrepairable, runtime, or scope-less blockers do not start a vague repair loop.
- Existing bounded loop behavior remains compatible.
- Existing repair policy limits still stop the loop.
- The plan is JSON-ready for CLI, daemon, and future web UI consumers.
