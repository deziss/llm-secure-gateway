FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GPU_AGENT_PORT=9400
WORKDIR /app

# NVIDIA NVML access: nvidia-ml-py. The NVIDIA container runtime injects libnvidia-ml.
COPY pyproject.toml ./
COPY llm_gpu_agent ./llm_gpu_agent
RUN pip install --no-cache-dir ".[nvidia]"

RUN useradd --system --uid 10001 agent
USER agent

EXPOSE 9400
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('GPU_AGENT_PORT','9400')+'/healthz', timeout=2)" || exit 1
ENTRYPOINT ["llm-gpu-agent"]
