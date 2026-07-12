# Provider Evidence Envelope Design

## Goal

Finish the constrained OpenRouter route hardening without another live workflow run by making read-only provider evidence selection deterministic in its prompt context.

## Decision

`WorkflowEngine` will include a machine-authored `provider_result_evidence_envelope` in every read-only verifier/evaluator context. When at least one manifest artifact exists, the envelope contains exactly one manifest-backed artifact pair and maps every required checkpoint to that path. The provider prompt instructs the role to copy that envelope verbatim into `artifacts` and `checkpoint_evidence`.

## Constraints

- Do not change `role-result/v1` validation or auto-repair provider output.
- Do not change executor routing, defaults, fallback behavior, or the OpenRouter allowlist.
- Do not perform another live provider or workflow run in this slice.
- Cover the context and prompt contract with offline tests.

## Acceptance

- A verifier prompt contains a manifest-backed envelope with the exact checkpoint mapping and SHA-256.
- The prompt directs read-only providers to copy the envelope verbatim and not add artifacts.
- Existing workflow, executor, and evidence-report tests remain green.
