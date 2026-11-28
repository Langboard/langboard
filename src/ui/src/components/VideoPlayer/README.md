# Shared video player

Uses the pinned Apache-2.0 `@videojs/react` 10.3.0 `NeutralVideoSkin`. Controls, keyboard interaction, settings, fullscreen, and storyboard preview UI come from upstream. No custom player controls or thumbnail UI are maintained here. The upstream notice and Apache-2.0 license are distributed under `public/licenses/videojs/`.

The current-session attachment transport is shared with protected images. Only native attachment URLs on the configured API origin use the authenticated API client. Blob requests are aborted and object URLs revoked when the source, session, or component lifecycle changes; external sources retain their ordinary browser transport.

The upstream stylesheet is imported as raw text and emitted using React 19's deduplicated stylesheet resource. This preserves upstream CSS layers without passing them through the application's Tailwind 3 layer optimizer.

Native `<track kind="metadata" label="thumbnails" default>` children pass through to Video.js. The packaged skin displays storyboard frames over the seek bar. The regression fixture includes a small real FFmpeg-generated storyboard. For native attachments in the current card editor, the player resolves one matching attachment and requests authorized storyboard assets from the generic backend endpoint. Session changes abort requests and revoke both sprite and VTT object URLs. Missing or denied previews leave video playback available. The fixture is not evidence that production thumbnails work.

Run the browser regression with `node node_modules/@playwright/test/cli.js test -c playwright.video-player.config.ts` from `src/ui`. It uses installed Chrome and intercepted attachment responses, not production credentials.
