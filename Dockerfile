# syntax=docker/dockerfile:1

FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN python -m venv "${VIRTUAL_ENV}"

COPY requirements.lock ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --requirement requirements.lock

COPY pyproject.toml ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps . \
    && python -m pip check \
    && python -m pip uninstall --yes pip


FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app /data \
    && chown app:app /data

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

USER app

EXPOSE 8000

CMD ["python", "-m", "azure_ipranges_api"]
