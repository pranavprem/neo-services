FROM python:3.11-slim

# System deps for whisper (ffmpeg for audio processing, build tools for native deps)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install setuptools first (pinned <82 — openai-whisper uses pkg_resources which was removed in 82+)
RUN pip install --no-cache-dir --upgrade pip "setuptools<82" wheel

# Install whisper deps that need building, then whisper itself
COPY requirements.txt .
RUN PIP_NO_BUILD_ISOLATION=0 pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Model cache directory
VOLUME /models

EXPOSE 9500

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9500/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9500"]
