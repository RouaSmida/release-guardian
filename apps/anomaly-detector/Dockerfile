# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /install /usr/local
WORKDIR /app
COPY detector ./detector
USER 10001
EXPOSE 8080
CMD ["python", "-m", "detector.main"]
