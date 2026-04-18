# Profile: rf-dissertation-general

## 1. Identity

- Profile ID: `rf-dissertation-general`
- Workflow lane: `reference-only`
- Unit kind: `dissertation-regulation`
- Status: `official`
- Official-only sources: `yes`

## 2. Scope And Applicability

- Normalized file: `meta/standards/normalized/rf-dissertation-general.md`
- Raw directory: `meta/standards/raw/rf-dissertation-general`
- Applicability notes:
- Reference-only federal dissertation regulation profile for cross-checking broader dissertation rules.

## 3. Official Sources

- `rf-dissertation-pravo-gov-2013`: Официальное опубликование правового акта на publication.pravo.gov.ru
  - URL: https://publication.pravo.gov.ru/document/0001201310010030
  - Final URL: https://publication.pravo.gov.ru/document/0001201310010030
  - Source date: 2013-10-01
  - Local file: not downloaded yet
  - Refresh error: <urlopen error _ssl.c:1063: The handshake operation timed out>
- `rf-dissertation-gost-page`: Страница protect.gost.ru с релевантным нормативным контекстом
  - URL: https://protect.gost.ru/document.aspx?catalogid=22&control=13&page=1745&search=
  - Final URL: https://protect.gost.ru/document.aspx?catalogid=22&control=13&page=1745&search=
  - Source date: not specified
  - Local file: not downloaded yet
  - Refresh error: <urlopen error [Errno 8] nodename nor servname provided, or not known>

## 4. Operative Precedence And Conflict Flag

- Conflict flag: `no`
- Operative precedence: No conflict flag declared in the registry metadata.

## 5. Refresh State

- Last refresh: `2026-04-18T09:11:27.412238+00:00`
- Manifest: `meta/standards/raw/rf-dissertation-general/manifest.json`

## 6. Workflow Notes

- Thesis/article workflows may bind this profile only when lane compatibility is explicit. Current lane: `reference-only`.
- Stable mode default is preserved: the profile is not refreshed automatically during workflow runs.

## 7. Finalization Impact

- Finalizer may rely on this normalized profile only together with the corresponding raw bundle state.
- Missing or partial raw material remains a blocker for claiming full formal compliance.
- If conflict metadata is flagged, the newest declared source stays operative but the checklist must preserve the conflict note.
