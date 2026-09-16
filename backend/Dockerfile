FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 evidenceflow && \
    mkdir -p /data && \
    chown -R evidenceflow:evidenceflow /app /data

USER evidenceflow

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
