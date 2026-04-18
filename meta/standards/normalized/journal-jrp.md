# Profile: journal-jrp

## 1. Identity

- Profile ID: `journal-jrp`
- Workflow lane: `article`
- Unit kind: `journal`
- Status: `official`
- Official-only sources: `yes`

## 2. Scope And Applicability

- Normalized file: `meta/standards/normalized/journal-jrp.md`
- Raw directory: `meta/standards/raw/journal-jrp`
- Applicability notes:
- Per-journal article profile. Refresh on demand because the site may change author requirements without versioned releases.

## 3. Official Sources

- `journal-jrp-home`: Journal of Russian Law platform home
  - URL: https://jrp.jes.su/
  - Final URL: https://jrp.jes.su/
  - Source date: not specified
  - Local file: meta/standards/raw/journal-jrp/journal-jrp-home.html
- `journal-jrp-rules`: Journal of Russian Law rules for authors
  - URL: https://jrp.jes.su/rules-jrp.html
  - Final URL: https://jrp.jes.su/rules-jrp.html
  - Source date: not specified
  - Local file: meta/standards/raw/journal-jrp/journal-jrp-rules.html

## 4. Operative Precedence And Conflict Flag

- Conflict flag: `no`
- Operative precedence: No conflict flag declared in the registry metadata.

## 5. Refresh State

- Last refresh: `2026-04-18T09:07:14.473031+00:00`
- Manifest: `meta/standards/raw/journal-jrp/manifest.json`

## 6. Workflow Notes

- Thesis/article workflows may bind this profile only when lane compatibility is explicit. Current lane: `article`.
- Stable mode default is preserved: the profile is not refreshed automatically during workflow runs.

## 7. Finalization Impact

- Finalizer may rely on this normalized profile only together with the corresponding raw bundle state.
- Missing or partial raw material remains a blocker for claiming full formal compliance.
- If conflict metadata is flagged, the newest declared source stays operative but the checklist must preserve the conflict note.
