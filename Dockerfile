FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY server.py ./server.py
COPY query_library.py ./query_library.py
COPY agent_service.py ./agent_service.py
COPY mcp ./mcp
COPY ontology_admin.py ./ontology_admin.py
COPY ontology_assistant.py ./ontology_assistant.py
COPY ontology ./ontology
COPY ontologies ./ontologies
COPY queries ./queries
COPY docs ./docs
COPY .agents ./.agents
COPY static ./static

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home appuser \
    && install -d -o 10001 -g 10001 -m 0700 /app/logs /app/published_queries
USER 10001:10001

EXPOSE 8088 8090 8091
CMD ["python", "server.py"]
