# Plan: Unified Forgejo CI Workflow (`ci.yml`)

## Goal

Create a `.forgejo/workflows/ci.yml` on the `main` branch that mirrors the GitHub Actions unified CI pattern — run unit tests on every push/PR, and conditionally build, retag & push the Docker image to the Forgejo container registry on the `release` branch.

---

## Current State

| File | Branch | Purpose |
|------|--------|---------|
| `.github/workflows/ci.yml` | main | GitHub Actions: `test` + `publish` to GHCR (reference implementation) |
| `.forgejo/workflows/unittest.yml` | main | Forgejo: unit tests only (to be disabled, kept for reference) |
| `.forgejo/workflows/integration-test.yml` | main | Forgejo: manual integration tests (untouched) |
| `.forgejo/workflows/ci.yml` | release-gelse | Forgejo: test + publish (outdated — uses inline Dockerfile, pushes on `release-gelse` branch) |

### GitHub Actions reference (the pattern we mirror)

```yaml
# .github/workflows/ci.yml
test:
  - checkout → make ci-unit-test → upload artifact → dorny/test-reporter
publish:
  - needs: test
  - if: push to refs/heads/release
  - login to GHCR → make docker-build → retag → docker push
```

---

## Design Decisions (Confirmed by User)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Publish branch | `release` | Matches GitHub Actions convention, single canonical release branch |
| Build method | `make docker-build` + retag + `docker push` | `make docker-build` produces generic tags (`gelse/mdshare:latest`, `gelse/mdshare:$SHORT_SHA`) reusable across GitHub, Forgejo, and local testing. Each CI system retags for its own registry before pushing. |
| Old `unittest.yml` | Disable (rename to `.disabled`) | Keep for reference, not deleted |

---

## Proposed Workflow

### Job 1: `test`

| Aspect | Value |
|--------|-------|
| Trigger | All pushes + pull requests |
| Runner | `docker` |
| Test method | `make ci-unit-test` (Docker Compose `--profile test`) |
| Artifact | `actions/upload-artifact@v3` with `NODE_TLS_REJECT_UNAUTHORIZED: "0"` |
| Test reporter | **None** — `dorny/test-reporter@v1` is GitHub-specific and unavailable on Forgejo |

### Job 2: `publish`

| Aspect | Value |
|--------|-------|
| Trigger | Only on `push` to `refs/heads/release` |
| Dependency | `needs: test` |
| Runner | `docker` |
| Build | `make docker-build` → produces `gelse/mdshare:latest` + `gelse/mdshare:$SHORT_SHA` |
| Retag | `docker tag` to `${{ vars.REGISTRY_URL }}/${{ vars.REGISTRY_USER }}/mdshare:latest` and `:${{ github.sha }}` |
| Push | `docker push` both tags |
| Auth | `docker/login-action@v3` using `vars.REGISTRY_URL`, `vars.REGISTRY_USER`, `secrets.REGISTRY_TOKEN` |

**Note on short SHA:** Unlike the GitHub Actions workflow which uses `${GITHUB_SHA::7}` for a 7-char short SHA, the Forgejo version pushes the **full** `${{ github.sha }}`. The `make docker-build` target already tags with `$(git rev-parse --short HEAD)` (7 chars via the `VERSION` variable), keeping local/CI consistency.

---

## Required Variables & Secrets

Must be configured in the Forgejo repository/organization settings:

| Variable/Secret | Type | Description |
|-----------------|------|-------------|
| `vars.REGISTRY_URL` | Variable | Forgejo container registry hostname (e.g. `forgejo.gelse.local`) |
| `vars.REGISTRY_USER` | Variable | Registry username |
| `secrets.REGISTRY_TOKEN` | Secret | Registry access token/password |

*(These already exist on the Forgejo instance — the `release-gelse` workflow used them.)*

---

## Workflow YAML

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: docker
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Run unit tests (Docker Compose)
        run: make ci-unit-test

      - name: Upload test results
        uses: actions/upload-artifact@v3
        if: always()
        env:
          NODE_TLS_REJECT_UNAUTHORIZED: "0"
        with:
          name: test-results
          path: test-results/

  publish:
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/release'
    runs-on: docker
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Log in to Forgejo container registry
        uses: docker/login-action@v3
        with:
          registry: ${{ vars.REGISTRY_URL }}
          username: ${{ vars.REGISTRY_USER }}
          password: ${{ secrets.REGISTRY_TOKEN }}

      - name: Build image (generic tags via Makefile)
        run: make docker-build

      - name: Retag and push to Forgejo registry
        run: |
          SHORT_SHA="${GITHUB_SHA::7}"
          docker tag gelse/mdshare:latest     ${{ vars.REGISTRY_URL }}/${{ vars.REGISTRY_USER }}/mdshare:latest
          docker tag gelse/mdshare:${SHORT_SHA} ${{ vars.REGISTRY_URL }}/${{ vars.REGISTRY_USER }}/mdshare:${SHORT_SHA}
          docker push ${{ vars.REGISTRY_URL }}/${{ vars.REGISTRY_USER }}/mdshare:latest
          docker push ${{ vars.REGISTRY_URL }}/${{ vars.REGISTRY_USER }}/mdshare:${SHORT_SHA}
```

### Why the publish job mirrors GitHub Actions (not build-push-action)

```
                    make docker-build
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   gelse/mdshare:latest    gelse/mdshare:$SHA
          │              │
          │    (shared, registry-agnostic tags)
          │              │
   ┌──────┴──────────────┴──────┐
   │    CI-specific retag/push   │
   ├─────────────────────────────┤
   │ GitHub:  → ghcr.io/gelse/   │
   │ Forgejo: → $REGISTRY_URL/   │
   │ Local:   (no push)          │
   └─────────────────────────────┘
```

---

## Files to Change

| File | Action | Description |
|------|--------|-------------|
| `.forgejo/workflows/ci.yml` | **CREATE** | New unified CI workflow |
| `.forgejo/workflows/unittest.yml` | **RENAME** to `unittest.yml.disabled` | Disabled, kept for reference |

---

## Differences from the release-gelse Version

| Aspect | release-gelse (old) | main (new) |
|--------|---------------------|------------|
| Test method | Inline Dockerfile | `make ci-unit-test` (Docker Compose) |
| Publish branch | `release-gelse` | `release` |
| Build & push | `docker/build-push-action@v6` (single action) | `make docker-build` + retag + `docker push` |
| Tag format | Full `${{ github.sha }}` | 7-char `${GITHUB_SHA::7}` (matches `make docker-build`) |
| Test reporter | None | None (dorny unavailable on Forgejo) |

---

## Implementation Steps

1. **Create** `.forgejo/workflows/ci.yml` with the two jobs described above
2. **Rename** `.forgejo/workflows/unittest.yml` → `.forgejo/workflows/unittest.yml.disabled` (keep for reference)
3. **Commit** with message: `Add unified Forgejo CI workflow (test + publish), disable old unittest workflow`
