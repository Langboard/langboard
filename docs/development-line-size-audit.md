# Development-line size audit

Baseline: `42a3725c8a93796f1574dd066da7d4d536a64ad5`. Reviewed source: `5173d051c13c3959a2fabdc82f8e863ab80aa70c`.

This is a Git snapshot audit, not a deployment or completion claim. The reviewed source is 1,608 commits ahead of the specified baseline, with no baseline-only commits. No remote refresh is implied.

| Category | Changed files | Added text lines | Removed text lines | Net | Binary files |
| --- | ---: | ---: | ---: | ---: | ---: |
| delivery/config | 40 | 2845 | 837 | 2008 | 0 |
| docs | 44 | 3260 | 0 | 3260 | 0 |
| production | 1170 | 81111 | 7567 | 73544 | 3 |
| tests | 589 | 70901 | 1431 | 69470 | 0 |
| dependency artifacts | 32 | 2995 | 1361 | 1634 | 27 |

Classification is path-based: tests include test directories and `_test.py`, `.test.`, `.spec.`, and `.fixture.` names. Documentation includes Markdown/RST. Lockfiles and vendored package artifacts are separate. Binary files count as changed files but have no line metric. Production includes source assets and translations; this is not executable-code-only LOC.

| Hotspot | Baseline lines | Reviewed lines |
| --- | ---: | ---: |
| `src/shared/py/langboard_shared/domain/services/factory/CardService.py` | 552 | 2172 |
| `src/api/langboard/routes/board/BoardCardApi.py` | 556 | 1011 |
| `src/api/langboard/mcp_tools/CardMcp.py` | 226 | 1780 |
| `src/api/langboard/card_workspace/ports.py` | absent | absent |
| `src/api/langboard/card_workspace/native_adapter.py` | absent | absent |
| `src/api/langboard/external_import/importer.py` | absent | 549 |
| `src/api/langboard/external_import/contract.py` | absent | 200 |
| `src/api/langboard/routes/board/ExecutionReceiptApi.py` | absent | 62 |

The earlier card snapshot (493 commits / 300 files / net +12,744 lines) does not describe this reviewed head. Growth alone is not proof of duplication, but the complete stack cannot be called a net code reduction. Existing deletion and hydration changes do not establish a whole-stack CARVE pass. Fresh-install migration replay, terminology audit, runtime delivery and remaining app/outbox acceptance are separate unfinished gates.

Reproduce with `git rev-list --left-right --count BASE...HEAD`, `git diff --numstat BASE HEAD`, and `git show REF:PATH | wc -l`. Use the fixed revisions above; do not silently compare with a changing branch name.

Follow-up cleanup removes 24 superseded SDK binary artifacts (797,081 bytes) from the current checkout after checking tracked consumers. Current panel 0.2.1 and Python 0.2.13 wheel/sdist remain. Historical revisions retain their packages. Panel CI now resolves the UI-declared archive and verifies provenance digest and installed version. This is artifact/delivery cleanup, not production LOC reduction.
