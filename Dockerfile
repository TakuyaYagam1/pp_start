# syntax=docker/dockerfile:1.7

FROM python:3.14.5-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && \
    useradd --system --gid app --home-dir /app app

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip && \
    if [ -s requirements.txt ]; then python -m pip install -r requirements.txt; fi

COPY . .

RUN mkdir -p /app/logs && \
    chown -R app:app /app

USER app

CMD ["python", "-m", "app.main"]
