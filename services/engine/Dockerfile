FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The proprietary coefficient package is published to a private index and is
# absent from this build by design. Without it the service starts in reference
# mode and says so on every response. See docs/circadian-methodology.md.
ARG CRONIIXX_PRIVATE_INDEX=""
RUN if [ -n "$CRONIIXX_PRIVATE_INDEX" ]; then \
        pip install --no-cache-dir --extra-index-url "$CRONIIXX_PRIVATE_INDEX" \
            croniixx-phase croniixx-chrono ; \
    fi

COPY . .

RUN useradd --create-home --uid 10002 croniixx \
    && chown -R croniixx:croniixx /app
USER croniixx

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8002/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
