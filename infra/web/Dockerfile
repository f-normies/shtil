# Образ web (спека #15, 4.2): Caddy, статика обоих кабинетов и страница «/».
# Контекст — корень репозитория: docker buildx build -f infra/web/Dockerfile .

FROM node:24.22.1-trixie-slim AS build
# pnpm той версии, что в поле packageManager корневого package.json.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable pnpm
WORKDIR /src

# Сначала только манифесты: слой с зависимостями меняется реже кода.
# --frozen-lockfile требует package.json всех пакетов workspace.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/doctor/package.json apps/doctor/
COPY apps/patient/package.json apps/patient/
COPY packages/api-client/package.json packages/api-client/
COPY packages/ui/package.json packages/ui/
COPY packages/figma-snapshot/package.json packages/figma-snapshot/
RUN --mount=type=cache,id=pnpm,target=/var/cache/pnpm \
    pnpm install --frozen-lockfile --store-dir /var/cache/pnpm

COPY tsconfig.base.json ./
COPY packages/api-client packages/api-client
COPY packages/ui packages/ui
COPY apps/doctor apps/doctor
COPY apps/patient apps/patient
RUN pnpm --filter "./apps/*" build


FROM caddy:2.11.7
LABEL org.opencontainers.image.source="https://github.com/f-normies/shtil" \
      org.opencontainers.image.description="Клинический компас: Caddy и статика кабинетов"

COPY infra/web/Caddyfile /etc/caddy/Caddyfile
COPY infra/web/index.html /srv/root/index.html
COPY --from=build /src/apps/doctor/dist /srv/doctor
COPY --from=build /src/apps/patient/dist /srv/patient
# Ошибка в Caddyfile останавливает сборку, до стенда она не доходит.
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
