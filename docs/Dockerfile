FROM node:22-bookworm-slim AS base
ENV FORCE_COLOR=0
RUN corepack enable && corepack prepare yarn@1.22.22 --activate
WORKDIR /opt/docusaurus

FROM base AS dev
WORKDIR /opt/docusaurus
COPY . /opt/docusaurus/
EXPOSE 3000
CMD [ -d "node_modules" ] && yarn start --host 0.0.0.0 --poll 1000 || yarn install && yarn start --host 0.0.0.0 --poll 1000

FROM base AS runtime
COPY . /opt/docusaurus/
RUN yarn install --frozen-lockfile
EXPOSE 3000
CMD ["sh", "-c", "yarn build && exec yarn serve --host 0.0.0.0 --no-open"]

FROM runtime AS prod
RUN yarn build

FROM prod AS serve
EXPOSE 3000
CMD ["yarn", "serve", "--host", "0.0.0.0", "--no-open"]

FROM caddy:2-alpine AS caddy
COPY --from=prod /opt/docusaurus/Caddyfile /etc/caddy/Caddyfile
COPY --from=prod /opt/docusaurus/build /var/docusaurus