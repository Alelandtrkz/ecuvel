FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-test.txt ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

ARG INSTALL_TEST_REQUIREMENTS=false

RUN if [ "$INSTALL_TEST_REQUIREMENTS" = "true" ]; then \
        pip install --no-cache-dir -r requirements-test.txt; \
    fi

COPY . .

EXPOSE 8000
