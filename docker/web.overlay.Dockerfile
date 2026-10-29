# Registry-less rebuild of the dashboard image: the current frontend/dist (run `npm run build` first) onto an earlier build, which keeps
# its nginx configuration. Build context is frontend/dist (see docker-compose.overlay.yml). See api.overlay.Dockerfile.
FROM aegisflow-web:base
USER root
RUN rm -rf /usr/share/nginx/html/*
COPY . /usr/share/nginx/html
USER 101
