FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn[standard] apscheduler pillow

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 8000
CMD ["python", "/app/app.py"]
