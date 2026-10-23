# Image for the tfl-sheets-export Cloud Run Job: refreshes every tab of the project Google
# Sheet from BigQuery (scripts/export_to_sheet.py --all). Built by deploy.sh from a context
# holding only the files below, never the whole repository (.env, data/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir google-cloud-bigquery==3.46.2 google-auth==2.62.0 requests==2.34.2

COPY src/exports src/exports
COPY scripts/export_to_sheet.py scripts/export_to_sheet.py
COPY analytics/exports analytics/exports
COPY config/sheet_exports.json config/sheet_exports.json

# Runs as the job's service account (tfl-sheets-export); no credentials in the image.
USER nobody
CMD ["python", "scripts/export_to_sheet.py", "--all"]
