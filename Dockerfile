# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Backend and Serve
FROM python:3.11-slim
WORKDIR /app

# Install python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy backend files
COPY backend/ ./

# Copy built frontend files to backend static folder
COPY --from=frontend-builder /app/frontend/dist ./static

# Ensure /tmp exists
RUN mkdir -p /tmp

EXPOSE 5000

# Use gunicorn with high timeout (600s matching your gemini config)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "600", "app:app"]
