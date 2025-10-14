FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir google-cloud-storage google-cloud-kms httpx cryptography

COPY scripts/gcs_backup.py /app/gcs_backup.py

# Cloud Run Jobs can pass args; default just shows help
ENTRYPOINT ["python", "/app/gcs_backup.py"]
CMD ["--help"]

