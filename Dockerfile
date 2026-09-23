FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDGARIO_DATA_DIR=/app/data \
    EDGARIO_ALLOW_REGISTRATION=0 \
    PORT=8080
WORKDIR /app
RUN groupadd --gid 10001 edgario && useradd --uid 10001 --gid 10001 --no-create-home edgario
COPY server.py index.html bootstrap_accounts.json entrypoint.py ./
RUN mkdir -p /app/data && chown -R 10001:10001 /app/data && chmod 700 /app/data
EXPOSE 8080
CMD ["python", "entrypoint.py"]
