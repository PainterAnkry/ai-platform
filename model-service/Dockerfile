# ---------- Model service ----------
# Default build: a tiny OpenAI-compatible stub so the full provider chain
# (embedding -> reranker -> LLM over real HTTP) runs without any external API
# key and without a model download.
#
# Real-model build: pass --build-arg WITH_QWEN=true to also install the
# inference stack (requirements-qwen.txt, ~2.5 GB of wheels) and run the chat
# endpoint on a local checkpoint. Mount the weights and select the backend:
#
#   docker build --build-arg WITH_QWEN=true -t ai-platform-model-service-qwen .
#   docker run --rm -p 9000:9000 \
#     -v /path/to/Qwen3.5-0.8B:/models/qwen:ro \
#     -e MODEL_SERVICE_LLM_BACKEND=qwen -e QWEN_MODEL_PATH=/models/qwen \
#     ai-platform-model-service-qwen
#
# GPU inference additionally needs the CUDA build of torch and a container
# runtime with GPU access; the CPU path above works as-is.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG WITH_QWEN=false

COPY requirements.txt .
COPY requirements-qwen.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_QWEN" = "true" ]; then pip install --no-cache-dir -r requirements-qwen.txt; fi

COPY main.py .
COPY qwen_backend.py .

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
