# Contexto de build: carpeta raíz counter_audio_transcription. Base CPU para el piloto.
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HOME=/tmp/huggingface
WORKDIR /app
ARG WHISPER_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2
ENV BUNDLED_WHISPER_MODEL=${WHISPER_MODEL} WHISPER_LOCAL_PATH=/opt/models/whisper
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY worker/requirements.txt /app/requirements-worker.txt
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 torchcodec==0.7.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /app/requirements-worker.txt
# Pesos públicos incluidos en la imagen; no se incorpora ningún token.
RUN python -c "import os; from huggingface_hub import snapshot_download; snapshot_download(os.environ['BUNDLED_WHISPER_MODEL'], local_dir='/opt/models/whisper')"
COPY counter_common/ /app/counter_common/
COPY worker/ /app/worker/
CMD ["python", "-m", "worker.main"]
