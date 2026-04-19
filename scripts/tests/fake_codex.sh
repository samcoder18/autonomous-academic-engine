#!/usr/bin/env bash
# Minimal fake `codex` used by CI smoke-e2e.
#
# Emits enough structured output (including a fenced ```verdict``` block)
# for telegram_console.work_cli and orchestrator to process a run as if a
# real Codex agent had responded. Used by .github/workflows/ci.yml so we
# detect regressions in the runtime pipeline without calling OpenAI.
#
# Contract (informal):
#   - Always exits 0.
#   - Writes a single stdout message that is valid Markdown and contains
#     a verdict block the verdict-parser can ingest.
set -euo pipefail

ACTION="${CODEX_FAKE_ACTION:-write-section}"
LANE="${CODEX_FAKE_LANE:-thesis}"
WORK="${CODEX_FAKE_WORK:-biometrics-vkr}"
STATUS="${CODEX_FAKE_STATUS:-reviewed}"

cat <<EOF
# fake-codex run
work: ${WORK}
lane: ${LANE}
action: ${ACTION}

## Summary
Fake Codex produced a minimal artifact. This output is used only by
scripts/tests/fake_codex.sh in CI smoke-e2e.

## Status
status: ${STATUS}

\`\`\`verdict
{
  "verdict_version": "1",
  "lane": "${LANE}",
  "kind": "submission-evaluator",
  "status": "${STATUS}",
  "summary": "Fake Codex smoke run produced a minimal valid verdict.",
  "blockers": []
}
\`\`\`
EOF
