FROM node:22.18-alpine AS build
WORKDIR /app
# Older Node 22 images bundle a Corepack trust root that cannot verify newer
# pnpm registry signatures. Install the repository-pinned package manager
# directly so image builds do not depend on Corepack's moving key metadata.
RUN npm install --global pnpm@11.9.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
RUN pnpm --dir apps/web build

FROM nginx:1.29-alpine AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
