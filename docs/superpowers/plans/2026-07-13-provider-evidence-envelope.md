# Provider Evidence Envelope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make read-only OpenRouter verifier/evaluator evidence references deterministic without weakening `role-result/v1`.

**Architecture:** `WorkflowEngine` constructs a single manifest-backed envelope from the sandbox work manifest and node checkpoints. The provider-visible context and prompt expose the same envelope; provider output must copy it rather than transcribe arbitrary manifest records.

**Tech Stack:** Python standard library and `unittest`.

## Global Constraints

- No live provider or workflow run.
- No change to `academic_engine/role_result_contract.py`.
- No default OpenRouter route, fallback, wrapper, or dependency.
- OpenRouter remains restricted to `academic-source-verifier` and `academic-submission-evaluator`.

---

### Task 1: Inject a Read-Only Evidence Envelope

**Files:**
- Modify: `academic_engine/workflow_engine.py`
- Test: `tests/test_workflow_engine.py`

**Interfaces:**
- `_read_only_role_context()` includes `provider_result_evidence_envelope` with one valid `artifacts` entry and every node checkpoint in `checkpoint_evidence`.
- `_role_prompt()` requires read-only providers to copy the envelope verbatim.

- [x] Write a failing prompt/context test in `test_verifier_prompt_includes_read_only_artifact_manifest` for the envelope path, SHA-256, checkpoint mapping, and verbatim-copy instruction.
- [x] Run `python3 -m unittest tests.test_workflow_engine.WorkflowEngineTests.test_verifier_prompt_includes_read_only_artifact_manifest -v` and observe RED.
- [x] Pass node checkpoints into `_read_only_role_context()`, build the envelope from the first manifest record, reuse it in the prompt examples, and require exact copying with no extra artifact records.
- [x] Run `python3 -m unittest tests.test_workflow_engine tests.test_executors tests.test_openrouter_evidence_report -q`, `ruff check academic_engine/workflow_engine.py tests/test_workflow_engine.py`, and `git diff --check`.
- [x] Commit `academic_engine/workflow_engine.py` and `tests/test_workflow_engine.py` with `fix: provide read-only evidence envelope`.

### Task 2: Record the Offline Closeout

**Files:**
- Create: `docs/deploy/evidence/2026-07-13-openrouter-rc-offline-closeout.md`

**Interfaces:**
- Records offline provider-contract hardening and explicitly states that no new live run was made or asserted.

- [x] State the two allowed OpenRouter roles, unchanged Codex default, strict contract, offline checks, and live acceptance deferral.
- [x] Run `python3 -m unittest discover -s tests -q`, scoped Ruff, `git diff --check`, and a non-printing secret scan of the closeout.
- [x] Commit the spec, plan, and closeout, then push the existing branch.
