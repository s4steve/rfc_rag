FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir "mcp==2.3.0" "psycopg[binary]==3.3.8" pytest
COPY rfc_rag rfc_rag
COPY tests tests
COPY schema.sql .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "rfc_rag.server"]
