FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY trur trur
COPY web web
COPY data data
COPY scripts scripts
ENV PYTHONUNBUFFERED=1 LEXICON_DIR=/app/data WEB_DIR=/app/web
CMD ["uvicorn","trur.app:create_app","--factory","--host","0.0.0.0","--port","8080","--workers","1","--no-server-header","--timeout-graceful-shutdown","2"]
