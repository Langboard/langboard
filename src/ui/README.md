# Langboard UI

Langboard's web interface provides boards, cards, wikis and connected app panels.
See the [installation guide](../../README.md) to configure a complete deployment.

## Development and production images

The [UI Dockerfile](../../docker/ui-production.Dockerfile) provides two runtimes:

- The `development` target runs Vite on port 8080 and supports source updates.
- The default target serves the compiled production application with nginx.

Configure `API_URL`, `PUBLIC_UI_URL` and `SOCKET_URL` for your own deployment.
The UI and its API, socket service and shared TypeScript package must use
compatible versions.

Docker Compose Watch can synchronize UI source into a development container.
Use the paths and Compose project belonging to your installation. Changes to
dependencies, lockfiles or the shared TypeScript package require an image rebuild.
Synchronizing source into the production nginx image does not enable hot reload.

App panels that use compiled widget assets require those assets in the UI output
directory. The Vite development target does not generate them automatically.
