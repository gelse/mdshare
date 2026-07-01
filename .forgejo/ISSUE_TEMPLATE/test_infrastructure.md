---
name: Test infrastructure
about: Set up testing framework and fixtures for a backend module
title: "[Test] "
labels: ["testing"]
---

## Plan

Set up testing framework and fixtures for backend API tests.

### Tech
- **pytest** — zero-config, built-in assertions, fixtures, coverage
- **Flask test client** — built-in HTTP assertions for Flask routes (no extra dep needed)
- **httpx** — optional async-capable HTTP client for integration tests
- **Test directory:** `backend/__tests__/`

### Implementation steps

1. Install dev dependencies:
   ```
   pip install pytest pytest-cov httpx
   ```
2. Create/update `pytest.ini`:
   - `testpaths = backend`
   - `python_files = test_*.py`
   - `addopts = -v --tb=short --strict-markers`
3. Add `Makefile` targets:
   - `test` — `python -m pytest`
   - `test-watch` — `python -m pytest-watch -- --testmon`
   - `test-coverage` — `python -m pytest --cov=backend --cov-report=term-missing --cov-report=html`
4. Create `backend/__tests__/helpers/`:
   - `conftest.py` — session fixture: create temp `__data__/documents/` dir, set `MDSHARE_DATA_DIR` env var; after session: clean up
   - `fixtures.py` — factory functions for creating test documents (`make_document()`, `write_document()`, `make_upload_payload()`)
5. Update `.gitignore` entries:
   ```
   /coverage/
   htmlcov/
   .coverage
   .pytest_cache/
   backend/__tests__/__data__/
   ```

### Test methodology (repeat for each test)

1. **Evaluate goal** — what behavior must this test verify?
2. **Write/update test** — implement following Arrange / Act / Assert
3. **Check test is useful** — does it fail for the right reason? Is it testing behavior not implementation?
4. **Execute** — `make test` / `python -m pytest <file>`
5. **Check result** — all green? Coverage adequate? Any flaky tests?

### Dependencies
- **Depends on:** #2 (Upload API — need a route to test against)
- **Blocks:** all other test issues
- **Estimated effort:** 1-2h
