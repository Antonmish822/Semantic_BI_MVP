FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY server.py ./server.py
COPY queries ./queries
COPY static ./static

RUN useradd --system --uid 10001 --no-create-home appuser
USER 10001:10001

EXPOSE 8082
CMD ["python", "server.py"]
