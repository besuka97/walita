FROM python:3.12-slim

WORKDIR /app

COPY *.py .
COPY dashboard ./dashboard
COPY docker/start.sh /start.sh

RUN apt update && \
    apt install -y --no-install-recommends cron && \
    useradd --system --uid 1000 --no-create-home --shell /usr/sbin/nologin walita && \
    chmod u+s /usr/sbin/cron && \
    chmod 0555 /start.sh && \
    mkdir -p /app/srv && \
    chown -R 1000:1000 /app /start.sh && \
    rm -rf /var/lib/apt/lists/*

USER 1000:1000

CMD [ "/start.sh"]
