FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/app/backend \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

WORKDIR /app

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home appuser

FROM runtime AS dashboard
COPY backend/server.py ./backend/server.py
COPY backend/query_library.py ./backend/query_library.py
COPY backend/agent_service.py ./backend/agent_service.py
COPY backend/mcp ./backend/mcp
COPY backend/common ./backend/common
COPY backend/ontology_admin.py ./backend/ontology_admin.py
COPY backend/ontology_assistant.py ./backend/ontology_assistant.py
COPY backend/ontology ./backend/ontology
COPY ontologies ./ontologies
COPY backend/queries ./backend/queries
COPY backend/rules ./backend/rules
COPY backend/resources ./backend/resources
COPY .agents/skills/bitrix24-graphdb-sparql/SKILL.md ./.agents/skills/bitrix24-graphdb-sparql/SKILL.md
COPY frontend/static ./frontend/static
COPY backend/services/analytics ./backend/services/analytics

RUN install -d -o 10001 -g 10001 -m 0700 /app/logs /app/published_queries
USER 10001:10001

EXPOSE 8088 8090 8091
CMD ["python", "backend/server.py"]

FROM runtime AS mcp
COPY backend/mcp ./backend/mcp
COPY backend/common ./backend/common
COPY backend/ontology ./backend/ontology
COPY backend/ontology_admin.py ./backend/ontology_admin.py
COPY backend/queries ./backend/queries
COPY backend/resources ./backend/resources
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8090
CMD ["python", "-m", "mcp.mcp_server"]

FROM runtime AS agent
COPY backend/agent_service.py ./backend/agent_service.py
COPY backend/query_library.py ./backend/query_library.py
COPY backend/mcp ./backend/mcp
COPY backend/common ./backend/common
COPY backend/ontology ./backend/ontology
COPY backend/ontology_admin.py ./backend/ontology_admin.py
COPY backend/queries ./backend/queries
COPY backend/resources ./backend/resources
COPY .agents/skills/bitrix24-graphdb-sparql/SKILL.md ./.agents/skills/bitrix24-graphdb-sparql/SKILL.md
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs /app/published_queries
USER 10001:10001
EXPOSE 8091
CMD ["python", "backend/agent_service.py"]

FROM runtime AS ontology
COPY backend/services/ontology ./backend/services/ontology
COPY backend/services/analytics ./backend/services/analytics
COPY backend/common ./backend/common
COPY backend/ontology ./backend/ontology
COPY backend/ontology_admin.py ./backend/ontology_admin.py
COPY backend/ontology_assistant.py ./backend/ontology_assistant.py
COPY backend/queries ./backend/queries
COPY backend/resources ./backend/resources
COPY backend/rules ./backend/rules
RUN install -d -o 10001 -g 10001 -m 0700 /app/ontologies /app/logs
USER 10001:10001
EXPOSE 8094
CMD ["uvicorn", "services.ontology.app:app", "--host", "0.0.0.0", "--port", "8094", "--no-access-log"]

FROM runtime AS auth
COPY backend/services/auth ./backend/services/auth
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8092
CMD ["uvicorn", "services.auth.app:app", "--host", "0.0.0.0", "--port", "8092", "--no-access-log"]

FROM runtime AS analytics
COPY backend/services/analytics ./backend/services/analytics
COPY backend/common ./backend/common
COPY backend/queries ./backend/queries
COPY backend/resources ./backend/resources
COPY backend/rules ./backend/rules
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8093
CMD ["uvicorn", "services.analytics.app:app", "--host", "0.0.0.0", "--port", "8093", "--no-access-log"]

FROM node:22-alpine AS web-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend ./
RUN npm run build

FROM runtime AS gateway
COPY backend/common ./backend/common
COPY backend/services/gateway ./backend/services/gateway
COPY frontend/static ./frontend/static
COPY --from=web-build /app/frontend/static/react ./frontend/static/react
RUN install -d -o 10001 -g 10001 -m 0700 /app/logs
USER 10001:10001
EXPOSE 8088
CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8088", "--no-access-log"]
