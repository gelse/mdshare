# mdshare — minimal Markdown sharing service
# Single-container deployment (Flask + uvicorn, no nginx)

FROM python:3.13-slim

WORKDIR /app

# install dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy application (preserving the backend package structure)
COPY backend/ /app/backend/

EXPOSE 5000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "backend.asgi:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "4", "--timeout-keep-alive", "30"]
