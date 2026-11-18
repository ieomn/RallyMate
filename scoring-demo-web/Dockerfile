FROM node:24.22.3-bookworm-slim AS build
WORKDIR /app
ENV WRANGLER_SEND_METRICS=false DO_NOT_TRACK=1
COPY package.json package-lock.json ./
COPY scripts/patch-vinext-disconnected-response.mjs ./scripts/
RUN npm ci --no-audit --no-fund
COPY . .
COPY --from=scoring_assets level_grading_v1.json relative_reference_policy.json /src/rallymate_scoring/data/
COPY --from=pose_assets mhr70_display.json /src/rallymate_vision/pose/data/
ARG VITE_SENTRY_DSN=""
ARG VITE_SENTRY_ENVIRONMENT=production
ARG VITE_SENTRY_RELEASE=""
ARG VITE_SENTRY_TRACES_SAMPLE_RATE=0.05
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN VITE_SENTRY_ENVIRONMENT=$VITE_SENTRY_ENVIRONMENT \
    VITE_SENTRY_RELEASE=$VITE_SENTRY_RELEASE VITE_SENTRY_TRACES_SAMPLE_RATE=$VITE_SENTRY_TRACES_SAMPLE_RATE
RUN npm run build

FROM node:24.22.3-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production WRANGLER_SEND_METRICS=false DO_NOT_TRACK=1
# vinext is a build tool and the production SSR server; retain its runtime tree.
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/dist ./dist
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/package.json ./package.json
COPY --from=build --chown=node:node /app/instrument.server.mjs ./instrument.server.mjs
COPY --from=build --chown=node:node /app/observability ./observability
USER node
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
