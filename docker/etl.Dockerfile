FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY etl/ ./etl/
COPY db/ ./db/

# Strip CRLF in case the repo was checked out on Windows without .gitattributes.
RUN sed -i 's/\r$//' etl/entrypoint.sh etl/crontab.template \
    && chmod +x etl/entrypoint.sh

ENTRYPOINT ["/app/etl/entrypoint.sh"]
