# Pin the Python base directly so the build never falls back to HA's Alpine
# base image (which has no pip). Local add-ons may use any base; python:3.12-slim
# is multi-arch, so the right arch is pulled automatically.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
