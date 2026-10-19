FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

COPY docker/requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Only what the server imports: the shared contracts, the backend, the simulator (the demo drives it) and
# the exported design. No tests, no research code, no data.
COPY contracts ./contracts
COPY backend ./backend
COPY simulator ./simulator
COPY models ./models

# A non-root user owns the data volume.
RUN useradd --system --uid 10001 --home /nonexistent aegis && mkdir /data && chown aegis /data
USER aegis
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# --proxy-headers: behind nginx the client address arrives in X-Forwarded-For. The API is not published to
# the host, so only nginx can reach it and trusting the header from any peer on the private network is safe.
CMD ["python", "-m", "uvicorn", "--factory", "backend.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--log-level", "warning"]
