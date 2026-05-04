FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON=python3.11 \
    YT_DLP_NO_UPDATE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    python3.11 \
    python3.11-venv \
    libcudnn9-cuda-12 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies (layer cached unless pyproject.toml/uv.lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Pre-download Whisper model (~1.5 GB)
RUN python3.11 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')"

# Pre-download RapidOCR models (~15 MB)
RUN python3.11 -c "from rapidocr import RapidOCR; RapidOCR(params={'EngineConfig.onnxruntime.use_cuda': False})"

# Install application
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-install-deps \
    && uv cache clean

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["omniscribe"]
CMD ["--help"]
