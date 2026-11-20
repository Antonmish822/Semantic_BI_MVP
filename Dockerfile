FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir "rdflib>=7.0,<8" "openai>=1.80,<2"
COPY server.py ./server.py
COPY mcp ./mcp
COPY ontology_admin.py ./ontology_admin.py
COPY ontology_assistant.py ./ontology_assistant.py
COPY ontologies ./ontologies
COPY queries ./queries
COPY docs ./docs
COPY static ./static

RUN useradd --system --uid 10001 --no-create-home appuser \
    && install -d -o 10001 -g "$(id -g appuser)" -m 0700 /app/logs
USER 10001:10001

EXPOSE 8082
CMD ["python", "server.py"]
