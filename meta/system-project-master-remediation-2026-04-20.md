# Repo-Only Remediation Closeout (2026-04-20)

> Historical note (2026-04-21): references to `biometrics-vkr` below describe
> the former active work before migration to `starter-work`. They remain here
> as historical closeout context only.

Follow-up closeout for the findings captured in
[system-project-master-audit-2026-04-20.md](system-project-master-audit-2026-04-20.md)
and
[system-project-master-backlog-2026-04-20.md](system-project-master-backlog-2026-04-20.md).

This document does not rewrite the audit snapshot. It records what this
repo-only remediation wave closed and what intentionally remains open.

## Fixed In Repo-Only Wave

- `DOC-001` — `tests/README.md` rewritten around the current split regression architecture and deterministic `unittest discover` entrypoint.
- `DOC-002` — `README.md` now distinguishes thesis one-shot reports from dissertation one-shot reports and aligns output-path wording with live CLI behavior.
- `CI-001` — `.github/workflows/ci.yml` now runs `ruff format --check telegram_console tests`, closing the formatting blind spot between local verification and CI.
- `BASELINE-001` (process guardrail only) — `meta/master-protocol.md` now states that any strong repo-level `release-quality` / `fully final` claim requires a clean git snapshot and a fully green verification matrix.

## Still Open By Design

- `BASELINE-001` (operator step) — the repo still needs a future clean-snapshot rerun if someone wants to make a fresh release-quality claim on a clean tree.
- `STATE-001` — active-work blockers for `biometrics-vkr` remain intentionally untouched in this repo-only wave.
- `PROJ-001` — advisory coverage for the active work remains a work/process concern and was not addressed here.
- `OPS-001`, `RUNTIME-001`, `CAND-001` — deferred architectural and platform items remain in the master backlog unchanged.

## Verification Matrix For This Wave

```bash
python3 -m unittest discover -s tests -q
ruff check telegram_console/ tests/
ruff format --check telegram_console/ tests/
python3 -m telegram_console.work_cli skill-source-map audit --json
```
