# Role Result Contract Hardening Design

## Goal

Tighten `role-result/v1` so role outputs are accepted only when they satisfy an explicit machine contract: dependency-free JSON Schema documentation, runtime validation, role-specific required fields, evidence-backed success, and stable error codes.

## Context

`WorkflowEngine` is the execution authority for role runs. It builds role prompts, reads exactly one fenced `role-result` JSON block, validates identity, checkpoints, checkpoint evidence, artifacts, blockers, and evaluator verdicts, then decides whether a workflow can promote.

The current parser already fails closed for malformed output, identity mismatch, missing checkpoints, missing checkpoint evidence, incomplete artifact manifests, hash mismatch, blocked-without-blockers, evaluator verdict absence, write-scope violations, and deleted artifacts. The hardening should preserve that boundary and avoid a parallel result authority.

Runtime dependencies remain empty. The design follows the existing `verdict_parser.py` approach: schema files document the contract, while Python code performs deterministic validation without `jsonschema`.

## Recommended Approach

Use a public schema file plus a dependency-free validator.

1. Add `meta/schemas/role-result.schema.json` as the documented `role-result/v1` contract.
2. Add `academic_engine/role_result_contract.py` for executable validation and stable error-code generation.
3. Keep `WorkflowEngine` as the only caller that accepts or rejects role results.
4. Update the role prompt example to show the stricter contract and error-code expectations.

Alternatives considered:

- Python-only validation would be fast, but weaker as a contract for agents and documentation.
- Inlining more checks into `workflow_engine.py` would be minimal, but would make an already large runtime file harder to test and reason about.

## Contract Shape

The base `role-result/v1` payload remains centered on the existing fields:

```json
{
  "version": "role-result/v1",
  "workflow_id": "workflow-id",
  "role_run_id": "01-role-id",
  "role_id": "role-id",
  "work_id": "work-slug",
  "lane": "thesis",
  "action": "style-pass",
  "status": "succeeded",
  "checkpoints": ["context-loaded", "completed"],
  "checkpoint_evidence": {
    "context-loaded": ["works/demo/path/to/artifact.md"],
    "completed": ["works/demo/path/to/artifact.md"]
  },
  "blockers": [],
  "artifacts": [
    {
      "path": "works/demo/path/to/artifact.md",
      "sha256": "64-lowercase-hex-digest"
    }
  ],
  "verdict": null
}
```

The schema documents required fields, allowed statuses, artifact hash shape, blocker shape, and conditional rules. The runtime validator mirrors those rules without importing a JSON Schema implementation.

## Role-Specific Requirements

The validator derives role type from trusted engine-side `RoleNode.role_id`, not from a role-provided field.

All roles:

- must provide all base fields;
- must match exact workflow identity;
- must use status `succeeded`, `blocked`, or `failed`;
- must report every engine-assigned checkpoint;
- must map every engine-assigned checkpoint to at least one verified artifact;
- must include every created or modified artifact with a matching SHA-256;
- must use structured blockers with category, code, message, repairable flag, optional blocked statuses, and optional details.

Evaluator roles:

- `thesis-submission-evaluator` and `academic-submission-evaluator` must provide a structured `verdict` object;
- a malformed or lane-mismatched verdict blocks submission readiness with a verdict-specific code;
- evaluator write attempts still fail closed through existing write-scope enforcement.

Evidence and verification roles:

- source acquirers, source verifiers, evidence cartographers, and citation checkers cannot report `succeeded` unless checkpoint evidence points to at least one verified artifact;
- role blockers must identify primary-support, citation, dynamic-material, verification, or process issues by code rather than by free text alone;
- unsupported or stale-source situations are represented as blockers, not as successful empty output.

Drafting, structure, style, critic, intake, and repair roles:

- successful results must still be checkpoint-backed by verified artifacts, even if the artifact is an unchanged target used as evidence for a no-op pass;
- blocked or failed results must preserve unresolved blockers with stable codes;
- repair roles must not drop unresolved blockers silently.

Finalizer roles:

- `academic-finalizer` cannot report `succeeded` without finalization checkpoint evidence and final bundle artifacts;
- it must preserve blockers instead of claiming successful finalization when required outputs or readiness gates remain incomplete.

## Error Codes

Validation returns blockers whose automation-relevant identity is the code. Human messages remain available for operators, but downstream behavior must key off codes and details.

Initial stable contract error codes:

- `role-result-block-missing`
- `role-result-block-count-invalid`
- `role-result-json-invalid`
- `role-result-schema-invalid`
- `role-result-identity-mismatch`
- `role-result-status-invalid`
- `role-result-checkpoint-missing`
- `role-result-artifacts-invalid`
- `role-result-artifact-hash-mismatch`
- `role-result-artifact-manifest-incomplete`
- `role-result-checkpoint-evidence-missing`
- `role-result-checkpoint-evidence-invalid`
- `role-result-success-without-evidence`
- `role-result-success-with-blockers`
- `role-result-blocked-without-blockers`
- `role-result-failed-without-blockers`
- `role-result-blocker-schema-invalid`
- `role-result-blocker-code-invalid`
- `role-result-evaluator-verdict-missing`
- `role-result-evaluator-verdict-invalid`
- `role-result-finalizer-artifact-missing`
- `role-result-role-contract-invalid`

Existing public blocker codes can be preserved as compatibility aliases where tests or runtime surfaces already depend on them. New validation code should emit the stable codes above.

## Data Flow

1. `WorkflowEngine` runs a role and reads the output file.
2. It extracts exactly one fenced `role-result` block.
3. It parses JSON.
4. It calls `validate_role_result_payload(payload, context)`.
5. The validator checks base shape, identity, status, artifacts, checkpoint evidence, blockers, verdict requirements, and role-specific rules.
6. On success, it returns normalized role-result data for `WorkflowEngine`.
7. On failure, it returns deterministic blockers and `WorkflowEngine` marks the role and workflow failed.

The validator does not read global workspace configuration and does not promote artifacts. It receives all required context from `WorkflowEngine`: workflow identity, node, sandbox path, post-run manifest, changed paths, and lane/action.

## Testing

Use TDD before implementation. Add focused tests for:

- invalid base shape fails with `role-result-schema-invalid`;
- unsupported status fails with `role-result-status-invalid`;
- `succeeded` with missing checkpoint evidence fails with `role-result-success-without-evidence`;
- `succeeded` with blockers fails with `role-result-success-with-blockers`;
- `blocked` without blockers fails with `role-result-blocked-without-blockers`;
- malformed blocker code fails with `role-result-blocker-code-invalid`;
- evaluator success without verdict fails with `role-result-evaluator-verdict-missing`;
- finalizer success without finalization artifact fails with `role-result-finalizer-artifact-missing`;
- a valid existing role result still promotes through the workflow engine;
- compatibility aliases keep existing fail-closed tests meaningful where needed.

The test target should be `tests/test_role_result_contract.py` for unit-level contract behavior plus narrow integration assertions in `tests/test_workflow_engine.py` where the workflow state is important.

## Rollout

Implement this as a runtime hardening change, not a schema-only documentation change.

1. Add failing contract tests.
2. Add the schema document.
3. Add the dependency-free validator.
4. Route `_parse_role_result()` through the validator.
5. Update prompt instructions and test helpers so generated role results match the stricter contract.
6. Run the focused test files first, then the broader relevant suite.

No new runtime dependency is introduced.
