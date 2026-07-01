.PHONY: test test-watch test-coverage test-integration

VENV = venv/bin/

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

# SSL is now handled by an external reverse proxy.
# The frontend-ssl service has been disabled in docker-compose.yml.
# # Generate self-signed SSL certificates for development
# certs:
# 	./scripts/generate-certs.sh ./certs
#
# # Start mdshare with HTTPS support
# ssl: certs
# 	docker compose --profile ssl up -d frontend-ssl
