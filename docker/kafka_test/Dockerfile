# Runtime for the arrivals producer and detector: Python plus the Kafka client only.
# The code is mounted read-only by compose.yaml, so edits need no rebuild.
FROM python:3.12-slim
RUN pip install --no-cache-dir confluent-kafka==2.10.0
WORKDIR /app
ENV PYTHONUNBUFFERED=1
