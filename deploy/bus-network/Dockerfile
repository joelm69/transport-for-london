# Image for the tfl-bus-network Cloud Run Job: polls every bus on the network and stores the
# poll's summaries in GCS (scripts/bus_network_snapshot.py). Built by deploy.sh from a context
# holding only the files below, never the whole repository (.env, data/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir google-auth==2.62.0 requests==2.34.2

COPY src/clients src/clients
COPY src/collectors/__init__.py src/collectors/__init__.py
COPY src/collectors/bus_network.py src/collectors/bus_network.py
COPY scripts/bus_network_snapshot.py scripts/bus_network_snapshot.py

# The TfL key arrives as TFL_API_KEY from Secret Manager; no credentials in the image.
USER nobody
CMD ["python", "scripts/bus_network_snapshot.py"]
