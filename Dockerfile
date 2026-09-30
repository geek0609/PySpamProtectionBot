#
#   SpamProtectionBot Docker Image
#
#   Multi-stage build: installs the bot and its dependencies into a virtual environment, then ships
#   it on a minimal Python image. The bot needs a configuration.yml (see configuration.sample.yml)
#   bind-mounted at /app/configuration.yml, and persists its SQLite database and Telegram session
#   under /data.
#

FROM python:3.13-alpine AS builder

WORKDIR /build

RUN python -m venv /opt/spb

COPY pyproject.toml LICENSE ./
COPY src ./src

RUN /opt/spb/bin/pip install --no-cache-dir .

FROM python:3.13-alpine

ARG BUILD_DATE
ARG REVISION
ARG VERSION=0.1.0

LABEL org.opencontainers.image.title="SpamProtectionBot" \
      org.opencontainers.image.description="Telegram spam-protection bot backed by the Open Federated Database specification" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.license="MIT"

RUN addgroup -S spb && adduser -S spb -G spb

WORKDIR /app

COPY --from=builder /opt/spb /opt/spb

RUN mkdir -p /data && chown -R spb:spb /app /data

USER spb

VOLUME ["/data"]

STOPSIGNAL SIGTERM

# The configuration and database paths are read from the environment rather than passed as
# arguments, so `docker run -e` or a compose file's `environment` can change them without replacing
# the entrypoint. Arguments given after the image name still take precedence over these.
ENV SPB_CONFIG=/app/configuration.yml \
    SPB_DATABASE=/data/database.db

ENTRYPOINT ["/opt/spb/bin/spb"]

# There is no HTTP endpoint to probe, since the bot only connects to Telegram outbound, so this is
# a basic liveness check confirming the process is still running, not a deep readiness check. It is
# in exec form because a shell running it would carry the pattern too, and always match itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["pgrep", "-f", "/opt/spb/bin/spb"]
