# Image generation profiles

Image generation is an optional internal AI capability. Its model and credentials
are separate from chat, vision transcription and embeddings. Image profiles are
not executable board bots and cannot be included in board templates as bots.

Install the `image-generation` extra when installing from source:

```sh
uv sync --locked --extra image-generation
```

This extra selects the existing locked Pillow raster validator. It does not
select a document converter, local model weights, Torch or CUDA. The maintained
API image includes the extra. Without it, the rest of the application remains
available and image preview returns an unavailable response.

## Configure and test

In internal AI settings, create an **Image generation** profile. Choose OpenAI,
OpenAI Compatible or LiteLLM, an explicit base URL and an image model ID. A model
catalog result is not evidence that the model supports image generation. The
provider must implement the images generation contract.

Credentials are write-only: the editor receives a configured flag, never the
saved key or its Vault locator. A blank edit preserves the saved key. Changing
the provider or endpoint requires explicitly supplied replacement credentials.
Public destinations use the existing HTTPS/DNS/Host/SNI policy. A private proxy
must match `MODEL_PROVIDER_ALLOWED_BASE_URLS` exactly; redirects are not followed.

Image profiles start disabled. After saving and enabling a profile, enter a test
prompt and explicitly confirm its transmission to the selected provider. A test
may incur provider charges. Merely opening settings or fetching the model list
does not generate an image. There is no automatic generation retry.

The initial adapter requests one square 1024px image. Leave quality and response
format at provider defaults unless the model supports the chosen options. The
[official image API reference](https://developers.openai.com/api/reference/resources/images/methods/generate)
documents model-dependent options; a chat-compatible endpoint alone is
insufficient. Base64 and public HTTPS result URLs are handled separately. The
generation credential is never forwarded to a returned image URL.

## Preview boundaries

The server bounds response size and decoded raster dimensions, accepts actual
PNG/JPEG/WebP images, rejects animation, strips metadata and returns a square
512px PNG. The editor shows 32/48/96px previews. A test does not change any board
or personal avatar and does not automatically gather board data.

Preview checks the current binding and credential before returning content.
Disable, deletion, endpoint/model change or key rotation invalidates a result
that was generated using the old configuration. Raster decoding runs outside
the event loop. At most two generations run per API process; excess requests
receive 429 immediately. This is a process resource bound, not a distributed
billing quota. Provider rate limits still apply.

Stopping the browser wait does not prove that provider work was cancelled or
that a charge was reversed. A timeout has the same billing uncertainty.

## Personal and board avatars

The profile page and board basic settings reuse one avatar generation dialog.
Opening it reads the current avatar, its revision and enabled profiles. It does
not send a prompt. Profile discovery exposes the approved model name and display
name, never provider endpoints or credentials. Board operations require current
read and update permission; personal operations target the authenticated user.

Edit the prompt, select a profile and confirm external transmission for each
generation. A preview remains local until **Apply** explicitly uploads it. Apply
checks the current avatar revision under a row lock; a stale revision returns a
conflict rather than replacing a newer image. The server normalizes the uploaded
raster using the same image boundary as previews. A failed database commit
cleans up only that request's newly created asset.

Current and previous avatars are persisted together. **Restore** swaps them
under the same revision check, including restoration to the original empty
avatar. This retains one previous state, not an unlimited asset history. Failed
generation, cancellation and closing a preview do not change the saved avatar.
Existing direct profile uploads remain available and advance the same history
and revision. Updated clients include the revision captured when selecting or
deleting a file; older clients without it retain their previous behavior.

These fields require additive migration `a28196e32054` after `f17085d21943`.
Source and disposable-database checks do not prove that migration has run on an
installation. Back up operational data and use the existing deployment migration
lane before deploying consumers of the new fields.

### Board context review

Preparing context is a separate, local-only action. It aggregates readable
**Shared** work-card counts by built-in workflow stage. It excludes Internal
(whisper), Private, archived and deleted cards, linked reference resources and
raw identifying text. No individual card title, body, attachment, board name,
person or secret is included in this prepared prompt. The response explicitly
records `external_request: false`.

Review and edit the prepared prompt before separately consenting to generation.
The aggregate counts alone do not identify a board's domain or purpose; add
public domain instructions explicitly. A semantic context agent that produces
distinctive development, marketing or documentation icons still needs its own
privacy and quality acceptance. This initial structural context does not
establish that broader requirement.

Authenticated canary migration, real provider generation, cross-user runtime
updates, durable restore after deployment and artistic readability remain
separate acceptance gates. Controlled component previews or source tests do not
prove them.
