FROM python:3.12-slim-bookworm@sha256:5d9e6adc9abd558bc0ce05f9ab16cef71328916fc9728f6b81e337af86a737e2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIONEER_DB=/data/pioneer.sqlite
WORKDIR /app
RUN groupadd --gid 10001 pioneer && useradd --uid 10001 --gid 10001 --no-create-home pioneer \
    && mkdir /data && chown 10001:10001 /data

# Caddy: 로그인 프록시. 특수 권한 표시를 지워야 일반 사용자로 실행할 수 있음 (root 단계에서 처리)
COPY --from=caddy:2.10.2-alpine /usr/bin/caddy /usr/bin/caddy
RUN python -c "import os; os.removexattr('/usr/bin/caddy', 'security.capability')" || true

COPY --chown=10001:10001 pioneer ./pioneer
COPY --chown=10001:10001 web ./web
COPY --chown=10001:10001 docs/methods.json ./docs/methods.json
COPY --chown=10001:10001 public-data/starter.sqlite.gz ./public-data/starter.sqlite.gz

USER 10001:10001
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"
CMD ["python", "-m", "pioneer.managed"]
