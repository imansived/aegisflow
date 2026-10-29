# Offline / registry-less rebuild: layers the CURRENT code onto an image that was built earlier from docker/api.Dockerfile.
# For machines that cannot reach a registry (docker.io timed out when this was written). The official, from-scratch build is
# docker/api.Dockerfile; use this only to verify the deployment composition (real Redis, real nginx, real containers).
#   docker tag aegisflow-api:latest aegisflow-api:base        # once, before the first overlay build
#   docker compose -f docker-compose.yml -f docker-compose.overlay.yml up -d --build
FROM aegisflow-api:base
USER root
RUN rm -rf /app/contracts /app/backend /app/simulator /app/models
COPY contracts ./contracts
COPY backend ./backend
COPY simulator ./simulator
COPY models ./models
USER aegis
