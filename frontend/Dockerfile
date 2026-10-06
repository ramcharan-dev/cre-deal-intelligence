FROM node:22-alpine AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci

# Local development: source is bind-mounted by docker compose.
FROM deps AS dev
COPY . .
EXPOSE 3000
# Inside the container Next.js always listens on 3000; compose maps it to host FRONTEND_PORT (3001).
CMD ["npx", "next", "dev", "--hostname", "0.0.0.0", "--port", "3000"]

FROM deps AS build
COPY . .
RUN npm run build

# Production image (standalone output).
FROM base AS runner
ENV NODE_ENV=production
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
ENV HOSTNAME=0.0.0.0 PORT=3000
CMD ["node", "server.js"]
