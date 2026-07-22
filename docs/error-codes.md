# Error reference
See contract/error-codes.json for the machine-readable closed enum. Conventions:
- Format NP-<PLANE>-<NNN>: EVT (write), CFG (config), READ, AUTH, RATE, SRV.
- Every error object: code, message, field_path, expected, received, retryable,
  retry_after?, doc_url, request_id. Batch rejections are per-item, index-aligned.
- retryable drives client behavior — never infer from HTTP status alone.
- TF-EVT-005 DUPLICATE_EVENT is informational (idempotent no-op), not a failure.
- TF-READ-002/003 are HTTP 200 statements about evidence/disclosure, not failures.
- Fix ownership per code: integrator | config_owner | platform | none — the first
  question of any debugging session, answered by the registry.
