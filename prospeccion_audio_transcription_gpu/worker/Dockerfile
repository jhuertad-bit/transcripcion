# Variante independiente. El Dockerfile CPU no se modifica.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HOME=/tmp/huggingface
WORKDIR /app
ARG WHISPER_MODEL=deepdml/faster-whisper-large-v3-turbo-ct2
ENV BUNDLED_WHISPER_MODEL=${WHISPER_MODEL} WHISPER_LOCAL_PATH=/opt/models/whisper
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY worker/requirements.txt /app/requirements-worker.txt
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 \
    && pip install --no-cache-dir -r /app/requirements-worker.txt \
    && pip check \
    && python -c "import torch, ctranslate2; assert torch.version.cuda is not None; print(torch.version.cuda)"
RUN python -c "import os; from huggingface_hub import snapshot_download; snapshot_download(os.environ['BUNDLED_WHISPER_MODEL'], local_dir='/opt/models/whisper')"
COPY prospeccion_common/ /app/prospeccion_common/
COPY worker/ /app/worker/
CMD ["python", "-m", "worker.gpu_main"]
