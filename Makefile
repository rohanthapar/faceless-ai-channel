.PHONY: install dev test lint fmt docker-up docker-down setup

# ── Dev setup ──────────────────────────────────────────────────
install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

setup: install
	cp -n .env.example .env || true
	mkdir -p data output/drafts output/rendered output/published logs credentials assets/tts_models assets/fonts assets/music
	@echo "✅ Setup complete. Edit .env with your credentials then run: make dev"

# ── Run ────────────────────────────────────────────────────────
dev:
	.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Tests ──────────────────────────────────────────────────────
test:
	.venv/bin/pytest tests/ -v --asyncio-mode=auto

test-cov:
	.venv/bin/pytest tests/ -v --asyncio-mode=auto --cov=app --cov-report=html

# ── Code quality ───────────────────────────────────────────────
lint:
	.venv/bin/ruff check app/ tests/

fmt:
	.venv/bin/ruff format app/ tests/

# ── Docker ─────────────────────────────────────────────────────
docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app

# ── Database ───────────────────────────────────────────────────
db-migrate:
	.venv/bin/alembic upgrade head

db-revision:
	.venv/bin/alembic revision --autogenerate -m "$(msg)"
