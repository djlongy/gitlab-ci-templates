# ─── Skeleton Dockerfile ─────────────────────────────────────────────
# Use this as a starting point. Key conventions:
#
# 1. ARG APP_VERSION=dev  — BuildKit injects the semver tag automatically
# 2. Multi-stage build    — keep final image minimal (Alpine or distroless)
# 3. Non-root user        — required by Kyverno policies
# 4. apk upgrade          — fix known CVEs in base packages
#
# For Python services, see the Vigil Dockerfile as reference.
# For Go services, use the distroless pattern below.
# For Node services, use node:22-alpine.

# ── Example: Python (FastAPI) ────────────────────────────────────────
# FROM python:3.12-alpine AS base
# ARG APP_VERSION=dev
# WORKDIR /app
# RUN apk upgrade --no-cache && \
#     apk add --no-cache postgresql-libs && \
#     apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev
# RUN pip install --no-cache-dir --upgrade pip
# COPY pyproject.toml ./
# RUN pip install --no-cache-dir . && apk del .build-deps
# COPY src/ ./src/
# ENV APP_VERSION=${APP_VERSION}
# RUN adduser -D app
# USER app
# EXPOSE 8000
# CMD ["uvicorn", "myapp.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Example: Go (distroless) ────────────────────────────────────────
FROM golang:1.24-alpine AS builder
ARG APP_VERSION=dev
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags="-s -w -X main.version=${APP_VERSION}" -o /server .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /server /server
EXPOSE 8080
ENTRYPOINT ["/server"]

# ── Example: Node (Alpine) ──────────────────────────────────────────
# FROM node:22-alpine
# ARG APP_VERSION=dev
# WORKDIR /app
# RUN apk upgrade --no-cache
# COPY package.json package-lock.json ./
# RUN npm ci --production
# COPY . .
# ENV APP_VERSION=${APP_VERSION}
# RUN adduser -D app
# USER app
# EXPOSE 3000
# CMD ["node", "server.js"]
