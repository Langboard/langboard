# Langboard UI

## Canary development on the existing Dokploy server

`docker/ui-canary-development.compose.yaml` selects the `development` target in
`docker/ui-production.Dockerfile`. It starts Vite without generating the complete
production bundle. The final/default Docker target remains the production nginx
runtime. The canary development service retains the existing `ui` network alias.

The source checkout is `/var/lib/langboard-development` on `langboard-lxc110`.
Keep changes committed and pushed to the existing integration PR; update this
checkout with `git pull --ff-only`. If the checkout is dirty, inspect and preserve
its changes before updating it.

After Dokploy has successfully built and started the development service, run
one Compose Watch process against the same project and generated Compose file:

```sh
docker compose -p compose-index-virtual-driver-nw8llh \
  --env-file /etc/dokploy/compose/compose-index-virtual-driver-nw8llh/code/.env \
  -f /etc/dokploy/compose/compose-index-virtual-driver-nw8llh/code/docker-compose.yml \
  watch --no-up
```

Watch syncs `src/ui/src` and restarts Vite when `vite.config.ts` changes. Updating
package manifests, lockfiles, SDK vendor files, or shared TypeScript requires a
new development image through Dokploy. Do not run a second Watch process for
the same service. The runtime limits are 2 GB RAM and 1.5 CPUs; they do not limit
the image build itself.

The development Compose uses Docker Official Images from ECR Public with the
same pinned Node digest as the default Docker Hub base. This avoids Docker Hub
anonymous pull limits without changing the selected image contents. When
updating the development Dockerfile, publish its commit before changing the
image tag and Git build-context commit in the Compose file.

Keep data volumes, existing service images, and known rollback images. Inspect
active builds before reclaiming build cache. Verify the deployed UI in the
in-app browser with an authenticated session before marking a product card
complete. A healthy development container alone does not prove feature E2E.

Opaque app panels require compiled widget assets in the UI output directory;
the current development target does not generate those assets. Validate those
features separately before accepting an app-panel card.
