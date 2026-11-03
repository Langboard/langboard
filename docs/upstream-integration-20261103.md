# Haseong upstream integration — 2026-11-03

The active upstream is Haseong Kim (`dynafer`)'s split repositories, not just the older `yamonco/langboard` monorepo. The old monorepo main (`45171c10973d0c96450f2d9f4603bd3617b41b99`) and release/0.2.0 are already ancestors of this integration branch; that does not establish parity with the split repositories.

Prefer upstream implementations. Preserve local behavior only where a concrete product or security requirement would otherwise regress. Split repository roots map to the following monorepo paths. Their histories have no common ancestor with this monorepo; importing a whole unrelated tree would overwrite local features and misrepresent integration progress.

| Upstream repository | Inspected main revision | Monorepo destination |
| --- | --- | --- |
| Langboard/api | 9c49c3c17b3ac567de780c1f38804fb434240e1a | src/api |
| Langboard/web-ui | f74415d037d7426b26b7d533aec10712254e96ec | src/ui |
| Langboard/shared-py | c3cb30e65f447e8d6ebe3ad5fad1fb252a6d365a | src/shared/py |
| Langboard/core-ts | f9c681cc950f59a6ec44b4d4278f83b938414629 | src/shared/ts |
| Langboard/socket | afac2aaca3b22d0c1d1304aabaff39284f67937e | src/socket |
| Langboard/graph | b6ad7ea761f111553a821851d1cb80dd89358527 | src/graph |

## Imported changes

| Upstream change | Integration |
| --- | --- |
| web-ui dfa6778b8: await wiki save | Use Sonner `.unwrap()` so the section save promise waits for persistence and propagates failure. Retain local optimistic revision/generation checks. |
| web-ui 235004123: wrap long comments | Apply upstream read-only `overflow-wrap:anywhere`; retain local draft recovery and constrained grid layout. |
| web-ui 38360b93d: favicon MIME | Use `image/x-icon` for the existing ICO file. |
| web-ui ca0b43540: chat stream ownership | Only an explicitly matching task settles local sending state or reports its failure. Every message still reconciles. Clear local reconnect recovery only for that matching terminal event. |
| socket 4c50f2bb7 + c2a3eab38: live task IDs | Stamp the server-owned task ID onto stream frames and declare the capability in the existing shared contract. Retain local `progress` events. Import the upstream interleaved-stream regression and include progress. |
| web-ui cdb6db0a4: connectivity recovery | Import upstream transport helper and online recovery effect with its five behavioral tests. Keep local draft initialization ownership. |
| web-ui f74415d03: isolate editor authentication helper | Move the identical helper into its upstream module and point the editor directly at it, retaining the transport re-export for existing consumers. Provider lifecycle changes remain under review below. |

## Conflicts requiring further integration

These are explicit outstanding work, not claims that the local design is better in general.

- **Editor provider teardown (`web-ui f74415d03`)**: upstream handles immediate disposal and late provider creation. Local initialization also serializes retries per editor, aborts draft restoration, fences readiness, and preserves authoritative drafts. Reconcile both lifecycle guarantees before replacing that effect; importing only the authentication module is not full adoption of this commit.
- **Editor authorization lookup optimization (`api 365b8a5bd`)**: upstream combines read/write lookup. Local authorization additionally enforces private-card audience, comment-author mutation, wiki generation, and admission ordering. Preserve those checks when reducing lookups; the upstream helper cannot replace the local route unchanged.
- **Shared Python cron mutation (`shared-py a003dd57d / 1871108ef`)**: upstream adds file/process locking, atomic cron writes, and Cython-safe static mutation decorators. Local code has non-root scheduler fallback and schedule admission history. Integrate atomicity and compiled descriptors without removing those requirements.
- **Core TypeScript client/server exports and entity contracts**: upstream package structure differs from local UI imports and richer card contracts. Migrate consumers and validate packed client/server artifacts together; do not add unused parallel types or swap the package wholesale.
- **Socket native storage/build changes and Graph runtime changes**: require mapped build-context review and native runtime tests. They have not been imported by this batch.
- **Split deployment topology (`Langboard/deploy`)**: sibling repositories and native images differ from the current monorepo development Compose. Changing deployment ownership is outside this source-import batch.

## Validation and release state

Run from the repository root:

```sh
node --experimental-strip-types --test \
  src/ui/src/core/providers/BoardChatProvider.streamOwnership.test.mjs \
  src/ui/src/pages/BoardPage/components/wiki/WikiContent.saveCompletion.test.mjs \
  src/ui/src/components/Editor/reconnectEditorTransport.test.ts
```

Result: 12 passed. Re-running the chat/wiki behavioral tests against the pre-import production callbacks fails four cases (unrelated failure, taskless replay, save completion, save rejection). Tests execute the production callbacks with controlled dependencies; they do not prove a mounted React/browser flow.

`git diff --check` passed. No dependency installation or local full build was performed. The imported Elixir regression, UI typecheck, native image validation, authenticated canary E2E, and the remaining conflict integrations are still pending. This batch does not justify closing product cards or declaring full upstream synchronization complete.
