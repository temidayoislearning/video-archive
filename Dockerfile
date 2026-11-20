FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ARCHIVE_DATA=/data HF_HOME=/data/.huggingface
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 archive \
    && install -d -o archive -g archive /data /data/videos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY archive.py .
COPY templates/ templates/
COPY static/ static/
USER archive
EXPOSE 8000
CMD ["python", "archive.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
