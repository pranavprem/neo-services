FROM python:3.11-slim

# Install curl for downloading voices, and piper binary deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Download piper binary (aarch64 for Apple Silicon Docker)
ARG PIPER_VERSION=2023.11.14-2
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then \
        PIPER_ARCH="aarch64"; \
    else \
        PIPER_ARCH="x86_64"; \
    fi && \
    curl -sL -o /tmp/piper.tar.gz \
        "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${PIPER_ARCH}.tar.gz" && \
    tar xzf /tmp/piper.tar.gz -C /usr/share && \
    rm /tmp/piper.tar.gz && \
    chmod +x /usr/share/piper/piper

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Voice model cache
VOLUME /models

EXPOSE 9501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9501/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9501"]
