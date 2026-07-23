# Publishing the SDK & handing off to customers (internal)

This is the team-facing runbook. **Nothing here ships to customers** — the
customer receives only the bundle produced by `scripts/build-customer-bundle.sh`.

## What the customer gets (and never gets)

| Ships to customer | Stays internal (never ships) |
|---|---|
| `truefigure-sdk` wheel (from `sdk/`) | `src/truefigure_server/` (server + engine) |
| `contract/openapi.yaml` | `ops/` (operator CLI) |
| `contract/events.schema.json` | `supabase/` (schema, migrations) |
| `contract/error-codes.json` | `tests/`, internal `docs/` |
| `sdk/README.md`, `sdk/INTEGRATION.md` | API keys / secrets (issued per tenant) |

## A. Build the hand-off bundle

```bash
./scripts/build-customer-bundle.sh
# -> dist/truefigure-customer-bundle-<version>.tar.gz
#    (wheel + contract + README + INTEGRATION.md; guarded against server leakage)
```

Hand this tarball to the customer, or publish the pieces (below).

## B. Publish the SDK

Bump `version` in `sdk/pyproject.toml`, then:

```bash
cd sdk
python3 -m build                 # builds sdk/dist/*.whl + *.tar.gz  (pip install build)
python3 -m twine check dist/*
```

- **Public PyPI:** `python3 -m twine upload dist/*` → `pip install truefigure-sdk`.
- **Private index** (recommended for controlled rollout): upload to your internal
  index (Artifactory / CodeArtifact / devpi); customers install with
  `pip install truefigure-sdk --index-url https://<your-index>/simple`.

Pre-flight:
- [ ] `cd sdk && python3 -m pytest tests/ --cov=truefigure --cov-fail-under=100` green
- [ ] wheel imports standalone and includes `error_codes.json` + `py.typed`
      (the build-customer-bundle guard checks the wheel is client-only)
- [ ] version bumped and tagged
- [ ] `CHANGELOG` / release notes updated

## C. Publish the contract (for non-Python integrators)

Host `contract/openapi.yaml` on a developer portal (Redoc/Swagger UI) so customers
can browse the API and generate clients in other languages. The wire contract is
identical to what the SDK sends.

## D. Provision each customer (issue credentials)

Use the operator CLI — secrets are shown **once** and only their hash is stored:

```bash
python3 ops/opsctl.py org create   …      # org + workspace
python3 ops/opsctl.py key issue    …      # -> tf_test_… and production keys
```

Deliver out-of-band (never via git / chat logs): the **API key(s)**, the **base
URL**, and a **webhook signing secret** if they subscribe to webhooks. Confirm the
customer can call `whoami()` before they start emitting.

## E. Ongoing

- Rotate/revoke keys via `opsctl` as needed.
- Error codes are a **closed registry** — never reuse a code; only add new ones,
  so customer `switch` statements stay valid across versions.
- Any change to `contract/*` is a wire-contract change: version it and regenerate
  the bundle.
