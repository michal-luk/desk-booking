FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_HOST=0.0.0.0 APP_PORT=2230
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home app && mkdir -p data static/uploads && chown -R app data static/uploads
USER app
EXPOSE 2230
VOLUME ["/app/data", "/app/static/uploads"]
CMD ["python", "run.py"]
