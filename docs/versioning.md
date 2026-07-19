# Versioning & compatibility policy
- schema_version in every event envelope; servers reject unsupported majors (TF-EVT-001).
- Within a major: additive-only. New optional fields, new enum values (consumers must
  tolerate unknown enum values on read), new endpoints.
- Never: field removal, field rename, type change, error-code reuse, natural-key change.
- Deprecations are documented and permanent aliases; the event vocabulary is treated as
  a wire protocol, with the same publish-and-pin discipline as the Measurement Standard.
- Client libraries are generated from openapi/openapi.yaml; hand-written logic is
  limited to batching, retry, buffering, and logging.
