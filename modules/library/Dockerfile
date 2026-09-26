FROM python:3.13-slim-trixie

# poppler-utils provides pdftotext for PDF indexing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY app/library.py /app/library.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python3", "/app/library.py", "serve"]
