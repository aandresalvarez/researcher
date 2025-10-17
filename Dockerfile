FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UAMM_PROFILE=core

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config

RUN pip install --no-cache-dir -e . && \
    useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
CMD ["uvicorn", "uamm.api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
