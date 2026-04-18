# Profile: journal-gip

## 1. Identity

- Profile ID: `journal-gip`
- Workflow lane: `article`
- Unit kind: `journal`
- Status: `official`
- Official-only sources: `yes`

## 2. Scope And Applicability

- Normalized file: `meta/standards/normalized/journal-gip.md`
- Raw directory: `meta/standards/raw/journal-gip`
- Applicability notes:
- Per-journal article profile. The RCSI and institute pages should be checked together on demand.

## 3. Official Sources

- `journal-gip-submissions`: Государство и право submissions page on RCSI
  - URL: https://journals.rcsi.science/1026-9452/about/submissions
  - Final URL: https://journals.rcsi.science/1026-9452/about/submissions
  - Source date: not specified
  - Local file: meta/standards/raw/journal-gip/journal-gip-submissions.html
- `journal-gip-author-rules`: Государство и право author rules on gipras.ru
  - URL: https://gipras.ru/pravila-dlya-avtorov.html
  - Final URL: https://gipras.ru/pravila-dlya-avtorov.html
  - Source date: not specified
  - Local file: meta/standards/raw/journal-gip/journal-gip-author-rules.html

## 4. Operative Precedence And Conflict Flag

- Conflict flag: `no`
- Operative precedence: No conflict flag declared in the registry metadata.

## 5. Refresh State

- Last refresh: `2026-04-18T09:07:14.496185+00:00`
- Manifest: `meta/standards/raw/journal-gip/manifest.json`

## 6. Workflow Notes

- Thesis/article workflows may bind this profile only when lane compatibility is explicit. Current lane: `article`.
- Stable mode default is preserved: the profile is not refreshed automatically during workflow runs.

## 7. Finalization Impact

- Finalizer may rely on this normalized profile only together with the corresponding raw bundle state.
- Missing or partial raw material remains a blocker for claiming full formal compliance.
- If conflict metadata is flagged, the newest declared source stays operative but the checklist must preserve the conflict note.
