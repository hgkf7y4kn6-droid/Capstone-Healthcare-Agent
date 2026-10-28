# ---- Web build ----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- API + static web ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production ASSISTANT_ENGINE=concierge
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
COPY --from=web /web/dist ./web/dist
RUN chmod +x docker/entrypoint.sh && useradd --create-home tendwell && chown -R tendwell /app
USER tendwell
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
