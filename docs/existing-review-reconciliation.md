# Existing review reconciliation

This record reconciles the existing review backlog on PR #710. It does not create a new pull request, imply that old PR heads contain these corrections, or assert deployment acceptance. Original branches and review history are retained.

## Verified changes

- Moved table constraints and indexes to the existing schema owner. PostgreSQL and SQLite DDL comparisons cover 131 tables with no semantic differences.
- Moved shared test fixtures into the test tree, removed fixture imports from consumer tests, and corrected cross-thread in-memory SQLite fixtures.
- Hoisted ordinary imports, fixed current primary identity revalidation, and reused the existing transaction for secret authorization.
- Preserved Docling section provenance and narrowed expected retrieval exceptions.
- Made inactive optional workflow stages unavailable for repair and replaced endpoint literals with canonical routing helpers.
- Corrected model hook dependencies and typed image alt/caption rendering.
- Removed credentials from graph checkpoint state while supporting credential rotation on resume.

## Executed local checks

| Check | Result |
| --- | --- |
| OAuth transport, provider discovery, disabled-provider isolation | 47 passed |
| Graph privacy, PostgreSQL checkpoint resume, agent loop | 33 passed |
| Workflow policy/API/HTTP and private child dispatch | 54 passed, 1 skipped |
| Secret input including PostgreSQL | 52 passed |
| Identity, proof, attachment and count regression | 124 passed |
| Relationship, work plan, execution and retrieval regression | 70 passed, 3 skipped |
| Import and retrieval regression | 58 passed |
| Image rendering browser regression | 7 passed |
| Workflow mapping browser regression | 6 passed |
| Receipt route current-role authorization | 16 passed |
| Localized marker checker | 10 passed |

Suites overlap; these numbers must not be added into a unique-test total. Browser checks above run local fixtures, not a deployed canary. A broader fixture run initially had one OAuth test failure caused by patching the old imported reference; the corrected OAuth suite passed. The initial receipt authorization run exposed the SQLite cross-thread fixture issue; its corrected suite passed. Failed logs are retained locally.

## Review-by-review disposition

“Source reviewed” below means current source and related regression were inspected. It does not resolve the original GitHub thread or prove live acceptance. Rows with unresolved acceptance or ownership are explicitly open.

### 1. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#pullrequestreview-5497114050)

Current Phoenix source has service authentication; live stream/resume acceptance remains required.

### 2. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6110906649)

Source reviewed; original-head reconciliation required

### 3. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#discussion_r4253297967)

Source reviewed; original-head reconciliation required

### 4. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#pullrequestreview-5497110905)

Source reviewed; original-head reconciliation required

### 5. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6113679991)

Source reviewed; original-head reconciliation required

### 6. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6113768233)

Source reviewed; original-head reconciliation required

### 7. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6113833506)

Source reviewed; original-head reconciliation required

### 8. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6114040390)

Source reviewed; original-head reconciliation required

### 9. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6114119984)

Source reviewed; original-head reconciliation required

### 10. PR #685

[Original review](https://github.com/yamonco/langboard/pull/685#issuecomment-6114250495)

Source reviewed; original-head reconciliation required

### 11. PR #684

[Original review](https://github.com/yamonco/langboard/pull/684#discussion_r4253285090)

Source reviewed; original-head reconciliation required

### 12. PR #684

[Original review](https://github.com/yamonco/langboard/pull/684#pullrequestreview-5497096933)

Source reviewed; original-head reconciliation required

### 13. PR #684

[Original review](https://github.com/yamonco/langboard/pull/684#issuecomment-6113254040)

Source reviewed; original-head reconciliation required

### 14. PR #684

[Original review](https://github.com/yamonco/langboard/pull/684#issuecomment-6113423591)

Source reviewed; original-head reconciliation required

### 15. PR #684

[Original review](https://github.com/yamonco/langboard/pull/684#issuecomment-6113508244)

Source reviewed; original-head reconciliation required

### 16. PR #480

[Original review](https://github.com/yamonco/langboard/pull/480#pullrequestreview-5497096084)

No source change requested in this review; external HMAC acceptance remains required.

### 17. PR #244

[Original review](https://github.com/yamonco/langboard/pull/244#pullrequestreview-5497095083)

Approval covers the reviewed delta only; it is not approval of the accumulated canary branch.

### 18. PR #334

[Original review](https://github.com/yamonco/langboard/pull/334#issuecomment-6110561566)

Source reviewed; original-head reconciliation required

### 19. PR #334

[Original review](https://github.com/yamonco/langboard/pull/334#issuecomment-6113754480)

Source reviewed; original-head reconciliation required

### 20. PR #606

[Original review](https://github.com/yamonco/langboard/pull/606#issuecomment-6111509339)

Source reviewed; original-head reconciliation required

### 21. PR #606

[Original review](https://github.com/yamonco/langboard/pull/606#issuecomment-6112253871)

Source reviewed; original-head reconciliation required

### 22. PR #606

[Original review](https://github.com/yamonco/langboard/pull/606#issuecomment-6113649854)

Source reviewed; original-head reconciliation required

### 23. PR #262

[Original review](https://github.com/yamonco/langboard/pull/262#issuecomment-6110608150)

Bootstrap now owns English defaults. The already applied historical migration still contains legacy Korean seed text; migration generation and immutable-history disposition remain open.

### 24. PR #262

[Original review](https://github.com/yamonco/langboard/pull/262#issuecomment-6112082716)

Source reviewed; original-head reconciliation required

### 25. PR #262

[Original review](https://github.com/yamonco/langboard/pull/262#issuecomment-6113407531)

Source reviewed; original-head reconciliation required

### 26. PR #243

[Original review](https://github.com/yamonco/langboard/pull/243#issuecomment-6110400669)

Source reviewed; original-head reconciliation required

### 27. PR #243

[Original review](https://github.com/yamonco/langboard/pull/243#issuecomment-6113263489)

Current source has the same-column guard and work-card deletion count. Live Dashboard reorder and Wiki/non-Wiki movement acceptance remains open.

### 28. PR #257

[Original review](https://github.com/yamonco/langboard/pull/257#issuecomment-6110473041)

Source reviewed; original-head reconciliation required

### 29. PR #257

[Original review](https://github.com/yamonco/langboard/pull/257#issuecomment-6112100228)

Source reviewed; original-head reconciliation required

### 30. PR #257

[Original review](https://github.com/yamonco/langboard/pull/257#issuecomment-6112139462)

Source reviewed; original-head reconciliation required

### 31. PR #257

[Original review](https://github.com/yamonco/langboard/pull/257#issuecomment-6113187561)

Source reviewed; original-head reconciliation required

### 32. PR #416

[Original review](https://github.com/yamonco/langboard/pull/416#issuecomment-6110545636)

Source reviewed; original-head reconciliation required

### 33. PR #416

[Original review](https://github.com/yamonco/langboard/pull/416#issuecomment-6113138385)

Source reviewed; original-head reconciliation required

### 34. PR #614

[Original review](https://github.com/yamonco/langboard/pull/614#issuecomment-6110452057)

Source reviewed; original-head reconciliation required

### 35. PR #614

[Original review](https://github.com/yamonco/langboard/pull/614#issuecomment-6113109129)

Source reviewed; original-head reconciliation required

### 36. PR #613

[Original review](https://github.com/yamonco/langboard/pull/613#issuecomment-6111047330)

Source reviewed; original-head reconciliation required

### 37. PR #613

[Original review](https://github.com/yamonco/langboard/pull/613#issuecomment-6111363771)

Source reviewed; original-head reconciliation required

### 38. PR #613

[Original review](https://github.com/yamonco/langboard/pull/613#issuecomment-6111801062)

Source reviewed; original-head reconciliation required

### 39. PR #613

[Original review](https://github.com/yamonco/langboard/pull/613#issuecomment-6113082004)

Original aggregate contains obsolete pre-Phoenix source. Preserve feature coverage before closing it.

### 40. PR #256

[Original review](https://github.com/yamonco/langboard/pull/256#issuecomment-6110939373)

Source reviewed; original-head reconciliation required

### 41. PR #256

[Original review](https://github.com/yamonco/langboard/pull/256#issuecomment-6111981950)

Source reviewed; original-head reconciliation required

### 42. PR #256

[Original review](https://github.com/yamonco/langboard/pull/256#issuecomment-6113074421)

Original aggregate needs replacement mapping before closure.

### 43. PR #255

[Original review](https://github.com/yamonco/langboard/pull/255#issuecomment-6110986063)

Source reviewed; original-head reconciliation required

### 44. PR #255

[Original review](https://github.com/yamonco/langboard/pull/255#issuecomment-6113069994)

Original aggregate needs replacement mapping before closure.

### 45. PR #560

[Original review](https://github.com/yamonco/langboard/pull/560#issuecomment-6111847960)

Source reviewed; original-head reconciliation required

### 46. PR #560

[Original review](https://github.com/yamonco/langboard/pull/560#issuecomment-6112356219)

Source reviewed; original-head reconciliation required

### 47. PR #419

[Original review](https://github.com/yamonco/langboard/pull/419#issuecomment-6110527315)

Source reviewed; original-head reconciliation required

### 48. PR #419

[Original review](https://github.com/yamonco/langboard/pull/419#issuecomment-6112317256)

Source reviewed; original-head reconciliation required

### 49. PR #419

[Original review](https://github.com/yamonco/langboard/pull/419#issuecomment-6113967601)

Source reviewed; original-head reconciliation required

### 50. PR #270

[Original review](https://github.com/yamonco/langboard/pull/270#issuecomment-6112070151)

Producer search in this checkout and the plugin checkout found no matching receipt template. Producer ownership and retroactive PR prose remediation remain open. No new language-enforcement workflow was enabled.

### 51. PR #270

[Original review](https://github.com/yamonco/langboard/pull/270#issuecomment-6112133408)

Balanced localized-example markers now reject unmatched end, nested start, and unclosed start. Ten standalone checker tests pass. Workflow activation remains separate.

### 52. PR #477

[Original review](https://github.com/yamonco/langboard/pull/477#issuecomment-6111907126)

Source reviewed; original-head reconciliation required

### 53. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111017154)

Source reviewed; original-head reconciliation required

### 54. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111238519)

Source reviewed; original-head reconciliation required

### 55. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111288028)

Source reviewed; original-head reconciliation required

### 56. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111303954)

Source reviewed; original-head reconciliation required

### 57. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111445638)

Source reviewed; original-head reconciliation required

### 58. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111550260)

Source reviewed; original-head reconciliation required

### 59. PR #680

[Original review](https://github.com/yamonco/langboard/pull/680#issuecomment-6111611532)

Original aggregate contains obsolete Node source; do not merge it wholesale.

### 60. PR #553

[Original review](https://github.com/yamonco/langboard/pull/553#issuecomment-6110849854)

Source reviewed; original-head reconciliation required

### 61. PR #597

[Original review](https://github.com/yamonco/langboard/pull/597#issuecomment-6110415641)

Source reviewed; original-head reconciliation required

### 62. PR #479

[Original review](https://github.com/yamonco/langboard/pull/479#issuecomment-6111383230)

Source reviewed; original-head reconciliation required

### 63. PR #479

[Original review](https://github.com/yamonco/langboard/pull/479#issuecomment-6112488519)

Source reviewed; original-head reconciliation required

### 64. PR #556

[Original review](https://github.com/yamonco/langboard/pull/556#issuecomment-6112907118)

Current checks gate drafts and use configured runners. Removed the catch-all UI path from localization; unrelated UI-only changes no longer select that workflow through this filter.

### 65. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6111467525)

Source reviewed; original-head reconciliation required

### 66. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6111756048)

Source reviewed; original-head reconciliation required

### 67. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6112440024)

Source reviewed; original-head reconciliation required

### 68. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6112741515)

Invocation credentials now use LangGraph Runtime context. Persisted tweaks and resume commands are redacted. Real PostgreSQL checkpoint versions and pending writes were checked across a new pool and rotated model credentials; graph privacy/agent suite: 33 passed.

### 69. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6112779522)

Graph redaction now follows shared sensitive marker matching, including prefixed/suffixed keys. Graph privacy/agent suite: 33 passed.

### 70. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6112851625)

Source reviewed; original-head reconciliation required

### 71. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6112899496)

Source reviewed; original-head reconciliation required

### 72. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6113091722)

Source reviewed; original-head reconciliation required

### 73. PR #681

[Original review](https://github.com/yamonco/langboard/pull/681#issuecomment-6113157839)

Same active Phoenix authentication integration concern as the first review; live acceptance remains open.

## Remaining acceptance

- Prove live Phoenix stream/resume authentication on the canary.
- Reconcile original aggregate PRs using per-feature replacement evidence before closure.
- Settle historical relationship migration provenance without silently rewriting applied revisions.
- Locate the automated collaboration receipt producer before enabling a language gate.
- Verify live Dashboard counts for reorder, Wiki/non-Wiki movement, and column deletion.
- Publish and run approved CI for this exact branch before claiming canary acceptance.
