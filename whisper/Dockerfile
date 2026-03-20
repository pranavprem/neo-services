FROM python:3.11-slim

# System deps for whisper (ffmpeg for audio processing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Model cache directory
VOLUME /models

EXPOSE 9500

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9500/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9500"]
