FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home appuser

FROM runtime AS dashboard
COPY server.py ./server.py
COPY query_library.py ./query_library.py
COPY agent_service.py ./agent_service.py
COPY mcp ./mcp
COPY common ./common
COPY ontology_admin.py ./ontology_admin.py
COPY ontology_assistant.py ./ontology_assistant.py
COPY ontology ./ontology
COPY ontologies ./ontologies
COPY queries ./queries
COPY rules ./rules
COPY docs ./docs
COPY .agents ./.agents
COPY static ./static
COPY services/analytics ./services/analytics

RUN install -d -o 10001 -g 10001 -m 0700 /app/logs /app/published_queries
USER 10001:10001

EXPOSE 8088 8090 8091
CMD ["python", "server.py"]

FROM runtime AS mcp
COPY mcp ./mcp
COPY common ./common
COPY ontology ./ontology
COPY ontology_admin.py ./ontology_admin.py
COPY queries ./queries
COPY docs ./docs
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8090
CMD ["python", "-m", "mcp.mcp_server"]

FROM runtime AS agent
COPY agent_service.py ./agent_service.py
COPY query_library.py ./query_library.py
COPY mcp ./mcp
COPY common ./common
COPY ontology ./ontology
COPY ontology_admin.py ./ontology_admin.py
COPY queries ./queries
COPY docs ./docs
COPY .agents ./.agents
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8091
CMD ["python", "agent_service.py"]

FROM runtime AS ontology
COPY services/ontology ./services/ontology
COPY services/analytics ./services/analytics
COPY common ./common
COPY ontology ./ontology
COPY ontology_admin.py ./ontology_admin.py
COPY ontology_assistant.py ./ontology_assistant.py
COPY queries ./queries
COPY docs ./docs
COPY rules ./rules
RUN install -d -o 10001 -g 10001 -m 0700 /app/ontologies /app/logs
USER 10001:10001
EXPOSE 8094
CMD ["uvicorn", "services.ontology.app:app", "--host", "0.0.0.0", "--port", "8094", "--no-access-log"]

FROM runtime AS auth
COPY services/auth ./services/auth
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8092
CMD ["uvicorn", "services.auth.app:app", "--host", "0.0.0.0", "--port", "8092", "--no-access-log"]

FROM runtime AS analytics
COPY services/analytics ./services/analytics
COPY common ./common
COPY queries ./queries
COPY docs ./docs
COPY rules ./rules
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8093
CMD ["uvicorn", "services.analytics.app:app", "--host", "0.0.0.0", "--port", "8093", "--no-access-log"]

FROM node:22-alpine AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web ./
COPY static/styles.css /app/static/styles.css
RUN npm run build

FROM runtime AS gateway
COPY services/gateway ./services/gateway
COPY static ./static
COPY --from=web-build /app/static/react ./static/react
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8088
CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8088", "--no-access-log"]
