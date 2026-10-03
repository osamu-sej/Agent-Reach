FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    HOST=0.0.0.0 \
    PORT=10000

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g mcporter@0.14.2

WORKDIR /app
COPY pyproject.toml README.md ./
COPY reach_research ./reach_research
RUN pip install --no-cache-dir '.[web,scrapling]' \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/reports/web \
    && chown -R app:app /app/reports

USER app
CMD ["reach-research-web"]
