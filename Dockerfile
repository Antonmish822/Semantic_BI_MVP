FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml ./pyproject.toml
RUN python - <<'PY'
import tomllib
from pathlib import Path
payload = tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))
deps = payload.get('project', {}).get('dependencies', [])
Path('/tmp/requirements.txt').write_text('\n'.join(deps), encoding='utf-8')
PY
RUN if [ -s /tmp/requirements.txt ]; then pip install --no-cache-dir -r /tmp/requirements.txt; fi
COPY server.py ./server.py
COPY agent_service.py ./agent_service.py
COPY mcp ./mcp
COPY queries ./queries
COPY docs ./docs
COPY static ./static

RUN useradd --system --uid 10001 --no-create-home appuser \
    && install -d -o 10001 -g "$(id -g appuser)" -m 0700 /app/logs
USER 10001:10001

EXPOSE 8082
CMD ["python", "server.py"]
