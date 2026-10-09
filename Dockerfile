FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies resolve from pyproject alone, so this layer survives edits to
# the package source.
COPY pyproject.toml README.md ./
COPY algo_trading/__init__.py ./algo_trading/
COPY strategies/__init__.py ./strategies/
RUN pip install --no-cache-dir .

COPY algo_trading/ ./algo_trading/
COPY strategies/ ./strategies/
RUN pip install --no-cache-dir --no-deps .

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin trader \
    && mkdir -p /data \
    && chown 1000:1000 /data
USER 1000

# LumiBot writes logs under the working directory and caches market data under
# HOME. Both point at /tmp so the container can run with a read-only root and a
# tmpfs there.
ENV HOME=/tmp
WORKDIR /tmp

VOLUME ["/data"]

# `live` runs every strategy in strategies/ unless STRATEGIES or arguments
# name some; `dashboard` serves the UI on 8080.
ENTRYPOINT ["algo-trading"]
CMD ["live"]
