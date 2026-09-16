FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app/src

COPY pyproject.toml README.md ./

# Install dependencies cleanly
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir ".[test]"

COPY src ./src
COPY tests ./tests
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY scripts ./scripts

# Create user
RUN useradd -m -u 1000 gateway && \
    chown -R gateway:gateway /app

USER gateway

EXPOSE 8000

CMD ["uvicorn", "llm_gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
