FROM python:3.12-slim-bookworm@sha256:5d9e6adc9abd558bc0ce05f9ab16cef71328916fc9728f6b81e337af86a737e2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIONEER_DB=/data/pioneer.sqlite
WORKDIR /app
RUN groupadd --gid 10001 pioneer && useradd --uid 10001 --gid 10001 --no-create-home pioneer \
    && mkdir /data && chown 10001:10001 /data
COPY --chown=10001:10001 pioneer ./pioneer
COPY --chown=10001:10001 web ./web
COPY --chown=10001:10001 docs/methods.json ./docs/methods.json
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"
COPY --from=caddy:2.10.2-alpine /usr/bin/caddy /usr/bin/caddy
CMD ["python", "-m", "pioneer.deployment"]
