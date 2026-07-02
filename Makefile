.PHONY: build test test-watch test-coverage test-integration ci-unit-test ci-test-direct

VENV = venv/bin/

# Auto-detect git hash; falls back to "unknown" when .git/ is absent.
VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown")

build:
	docker compose build --build-arg MDSHARE_VERSION=$(VERSION)

# Run test suite
test:
	$(VENV)python -m pytest

# Run tests in watch mode (rerun on file changes)
test-watch:
	$(VENV)python -m pytest-watch -- --testmon

# Run tests with coverage report
test-coverage:
	$(VENV)python -m pytest --cov=backend --cov-report=term-missing --cov-report=html

# Run integration tests against an external mdshare deployment.
# Requires DEPLOYMENT_HOST env var (e.g. http://192.168.1.100:5000).
# Optionally set DEPLOYMENT_MASTER_PASSWORD (default: test-integration-master-pw).
test-integration:
	$(VENV)python -m pytest tests/integration/ -v -m integration

# ---------------------------------------------------------------------------
# CI targets
# ---------------------------------------------------------------------------

# Forgejo CI — Docker-based test image (includes version injection).
ci-unit-test:
	docker build \
		--build-arg MDSHARE_VERSION=$(VERSION) \
		-t mdshare:test -f- . <<'EOF'
	FROM python:3.13-slim
	ARG MDSHARE_VERSION=unknown
	ENV MDSHARE_VERSION=$$MDSHARE_VERSION
	COPY backend/ /app/backend/
	COPY requirements-dev.txt /app/
	RUN pip install --no-cache-dir -r /app/backend/requirements.txt -r /app/requirements-dev.txt
	WORKDIR /app
	CMD ["python", "-m", "pytest", "backend", "--junitxml=/app/junit.xml"]
	EOF
	mkdir -p test-results
	docker run --name mdshare-test mdshare:test
	docker cp mdshare-test:/app/junit.xml test-results/junit.xml
	docker rm -f mdshare-test 2>/dev/null || true

# GitHub CI — direct pytest (no Docker, no venv).
ci-test-direct:
	pip install --no-cache-dir -r backend/requirements.txt -r requirements-dev.txt
	mkdir -p test-results
	python -m pytest backend --junitxml=test-results/junit.xml

# SSL is now handled by an external reverse proxy.
# The frontend-ssl service has been disabled in docker-compose.yml.
# # Generate self-signed SSL certificates for development
# certs:
# 	./scripts/generate-certs.sh ./certs
#
# # Start mdshare with HTTPS support
# ssl: certs
# 	docker compose --profile ssl up -d frontend-ssl
