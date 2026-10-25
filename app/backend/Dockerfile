FROM python:3.13-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.23
WORKDIR /app
COPY pyproject.toml uv.lock ./
# runtime deps only (numpy/pandas/lxml are project deps); no dev tools, no science group (sklearn, matplotlib,
# mediapipe) and no geo group (osmnx/networkx/shapely/scipy/pyarrow: experiments only)
RUN uv sync --frozen --no-dev --no-group science --no-group geo --no-install-project

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN groupadd --system --gid 10001 kcf \
 && useradd --system --uid 10001 --gid kcf --no-create-home --shell /usr/sbin/nologin kcf \
 && mkdir -p /var/lib/kcf /data/cache && chown kcf:kcf /var/lib/kcf
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY db ./db
# app.config.APP_ROOT resolves to / in this layout, so the heat experiment files the API reads at runtime
# (unit rules for 행정동 grouping, pooled results, 구 crosswalk) live under /backend/experiments/heat.
COPY experiments/heat/build_panel.py experiments/heat/common.py experiments/heat/weather_hub.py /backend/experiments/heat/
COPY experiments/heat/out/results.json experiments/heat/out/results_tmax_sensitivity.json experiments/heat/out/crosswalk_heat_to_2026.csv /backend/experiments/heat/out/
USER kcf
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
