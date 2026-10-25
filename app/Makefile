BACKEND_PORT ?= 8010
FRONTEND_PORT ?= 5190
DC := $(shell docker compose version >/dev/null 2>&1 && echo 'docker compose' || echo docker-compose)
# Ports 5173, 5174, 5180, 8600 are taken on this machine; do not use them.

.PHONY: docker-seed-db env install dev demo demo-check test lint exp figs docker docker-down docker-smoke reset-db seed-admin heat-ingest heat-eval modules-load verify-heat twin-build twin-warm

env:
	@test -f .env || { echo "KCF_SECRET_KEY=$$(openssl rand -hex 32)" > .env; grep -v '^KCF_SECRET_KEY=' .env.example >> .env; echo "created .env"; }

install:
	cd backend && uv sync
	cd frontend && npm ci

dev: env
	@set -a; . ./.env; set +a; export VWORLD_KEY=$$(security find-generic-password -a kcf -s VWORLD_KEY -w 2>/dev/null); \
	(cd backend && KCF_ENV=dev exec uv run uvicorn app.asgi:app --reload --timeout-graceful-shutdown 2 --port $(BACKEND_PORT)) & BP=$$!; \
	(cd frontend && VITE_BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT) exec npm run dev -- --port $(FRONTEND_PORT)) & FP=$$!; \
	trap 'kill $$BP $$FP 2>/dev/null' INT TERM EXIT; wait

# Production-like demo: no reload, one worker, env from .env, migrations + data + Ollama self-test
# (fails loudly), planner preloaded with a long keep_alive, frontend served from the Vite build.
# DEMO_VLM=1 also preloads the VLM. KCF_REQUIRE_OLLAMA=true in .env makes a missing Ollama fatal.
demo: env demo-check
	@set -a; . ./.env; set +a; export VWORLD_KEY=$$(security find-generic-password -a kcf -s VWORLD_KEY -w 2>/dev/null); \
	export KCF_WARMUP=true KCF_OLLAMA_KEEP_ALIVE=$${KCF_OLLAMA_KEEP_ALIVE:-3h} KCF_RATE_EXEMPT_LOOPBACK=$${KCF_RATE_EXEMPT_LOOPBACK:-true}; \
	(cd backend && exec uv run uvicorn app.asgi:app --host 127.0.0.1 --port $(BACKEND_PORT) --workers 1 \
	  --no-server-header --timeout-keep-alive 15 --timeout-graceful-shutdown 5) & BP=$$!; \
	(cd frontend && VITE_BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT) exec npm run preview -- --port $(FRONTEND_PORT)) & FP=$$!; \
	trap 'kill $$BP $$FP 2>/dev/null' INT TERM EXIT; \
	sleep 2; curl -sf http://127.0.0.1:$(BACKEND_PORT)/health/deep >/dev/null && echo "demo up: http://127.0.0.1:$(FRONTEND_PORT)  (API :$(BACKEND_PORT), /health/deep ok)" || echo "WARNING: /health/deep not ok yet"; \
	wait

# Startup self-test: migrations at head, datasets loaded, Ollama reachable + planner preloaded. Exit 1 = fix before demo.
demo-check: env
	@set -a; . ./.env; set +a; export KCF_OLLAMA_KEEP_ALIVE=$${KCF_OLLAMA_KEEP_ALIVE:-3h}; \
	cd backend && uv run python -m app.cli preflight --warm-up $(if $(DEMO_VLM),--vlm,) && \
	cd ../frontend && npm run build

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run mypy app experiments figures
	cd frontend && npm run build

exp: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m experiments.example_breast_cancer

figs:
	cd backend && uv run python -m figures.make_figures

docker: env
	$(DC) up --build

# Copy a consistent snapshot of the host DB (heat + module tables) into the compose volume. Run once before `make docker`.
docker-seed-db:
	@test -f backend/var/kcf.sqlite3 || { echo "no backend/var/kcf.sqlite3: run make heat-eval modules-load first"; exit 2; }
	@rm -rf .docker-seed && mkdir .docker-seed && sqlite3 backend/var/kcf.sqlite3 ".backup .docker-seed/kcf.sqlite3"
	$(DC) up --no-start backend
	docker run --rm --user 0 -v kcf-app_kcf_db:/v -v "$(CURDIR)/.docker-seed":/src:ro alpine sh -c 'cp /src/kcf.sqlite3 /v/kcf.sqlite3 && chown 10001:10001 /v/kcf.sqlite3'
	@rm -rf .docker-seed

docker-down:
	$(DC) down

# Build, start detached, probe /health/deep and the page through nginx, then stop.
docker-smoke: env
	$(DC) up --build -d
	@for i in $$(seq 1 30); do curl -sf http://localhost:$${FRONTEND_HOST_PORT:-8190}/api/health >/dev/null && break; sleep 2; done
	curl -sf http://localhost:$${FRONTEND_HOST_PORT:-8190}/api/health/deep; echo
	curl -sf -o /dev/null -w "page %{http_code}\n" http://localhost:$${FRONTEND_HOST_PORT:-8190}/
	$(DC) down

reset-db:
	rm -f backend/var/kcf.sqlite3 backend/var/kcf.sqlite3-wal backend/var/kcf.sqlite3-shm

# Usage: make seed-admin EMAIL=you@example.com   (password: KCF_ADMIN_PASSWORD env or prompt)
seed-admin: env
	@test -n "$(EMAIL)" || { echo "usage: make seed-admin EMAIL=you@example.com"; exit 2; }
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.cli seed-admin --email "$(EMAIL)"

# Idea A (heat-illness alerts). heat-ingest: regions, weather, cases, shelters -> SQLite (idempotent).
heat-ingest: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.heat.heat_all

# Panel -> ingest (needs the crosswalk the panel writes) -> evaluate -> figures (if present) -> persist.
heat-eval: env
	@set -a; . ./.env; set +a; cd backend && \
	uv run python experiments/heat/build_panel.py && \
	uv run python -m app.heat.heat_all && \
	uv run python experiments/heat/evaluate.py && \
	{ test ! -f experiments/heat/figures.py || uv run python experiments/heat/figures.py; } && \
	uv run python -m app.heat.persist && \
	uv run python -m app.modules.load

# 실버존(보행)·AED 밤 공백 산출물 -> SQLite. Idempotent; reloads only when a source sha256 or the region table changed.
modules-load: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.modules.load

# 재현 (live, <10 s, offline): pooled 2022-25 capture B1 vs OURS + paired block-bootstrap CI from the stored predictions,
# compared with experiments/heat/out/results.json. Exit 0 = identical. Also GET /heat/verify.
verify-heat: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.heat.verify

# Precompute /twin/<sgg>/scene files (needs the geo group for pyarrow; default 관악구 11620). make twin-build CODES="11620 11650"
twin-build: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.twin.precompute $(CODES)

# Precompute /twin state + actions for 관악구 on the demo dates (also runs at startup with KCF_WARMUP=true).
twin-warm: env
	@set -a; . ./.env; set +a; cd backend && uv run python -m app.twin.cache
