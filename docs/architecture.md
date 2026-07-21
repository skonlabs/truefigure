# TrueFigure — layered architecture

Logic is separated by responsibility so proprietary/core rules stay server-side
and consistent, while the SDK carries only client-side convenience.

```
python/truefigure/            SDK  — client-side convenience only
├── client.py                 file upload, pagination, retries, polling, timeouts, transport
├── events.py                 request construction + public-contract validation + local event_key
├── webhooks.py               webhook signature verification (consumer-side HMAC)
├── types.py                  language-specific typed results
└── errors.py                 error mapping (closed TF-<PLANE>-<NNN> registry)

src/truefigure_sdk/           SERVER
├── api/                      Application layer — authentication, orchestration, (de)serialization
│   ├── app.py                composition root (mounts routers, request-id, error envelopes)
│   ├── routes/               URL routers + controllers (config, ingest, read, imports, webhooks)
│   ├── request_models/       request parsing/validation (http) + wire-name mapping (wire)
│   ├── response_models/      the uniform response envelope
│   └── application_services/  authentication (auth)
├── domain/                   Core intelligence layer — reusable business rules (NEVER imports api)
│   ├── engine.py             the measurement engine (cost, seats, containment, time-savings)
│   ├── entities/             identity & value objects (event_key, id refs, periods)
│   └── policies/             identity-resolution pipeline, webhook delivery policy
├── platform/                 Platform layer — infrastructure (NEVER imports api)
│   ├── database/             Postgres pool + helpers (db)
│   ├── storage/              object storage backends (filesystem / Supabase)
│   ├── billing/              rate limiting + plan quota
│   ├── config/               settings + closed error-code registry
│   └── security/             at-rest secret encryption (secretbox)
└── errors.py                 cross-cutting error model
```

## Where each responsibility lives (per the reference layering)

| Concern | Layer | Module(s) |
|---|---|---|
| File handling, client validation, retries, polling, typed results, workflows | **SDK** | `python/truefigure/*` |
| Authentication, authorization, request orchestration, async-job mgmt, result aggregation | **API application** | `api/routes`, `api/application_services`, `api/request_models`, `api/response_models` |
| Engines, policy evaluation, confidence/margin, result ranking | **Core intelligence** | `domain/engine.py`, `domain/policies`, `domain/entities` |
| Storage, database, queues (workers), billing, observability, external providers | **Platform** | `platform/*` |

## Enforced invariants (checked by `ci.sh`)

- **The SDK holds no measurement/core logic** — `grep` guard over `python/truefigure`
  finds no engine/pricing/threshold code.
- **Clean layering** — `domain/` and `platform/` never import `api/`, so the core
  intelligence layer is independent of transport and controllers.
- The server is always authoritative: it re-validates, re-canonicalizes, recomputes
  the `event_key`, deduplicates, and performs all measurement.
