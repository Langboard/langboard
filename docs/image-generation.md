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

Board-context selection, user-reviewed outbound summaries, board avatar storage,
explicit apply and durable avatar restoration are separate acceptance work.
This provider setup and test preview do not establish those capabilities.
