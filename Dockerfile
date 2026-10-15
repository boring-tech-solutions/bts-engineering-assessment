FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 candidate
USER candidate

# Default: process the fixtures. Pass the API key at run time with --env-file .env.
CMD ["python", "run.py"]
