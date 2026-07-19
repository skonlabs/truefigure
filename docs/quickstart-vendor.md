# Quickstart: vendor-product embedding (origin=vendor_product)
Same schema, one difference: set origin="vendor_product" on every event. Embed once;
every buyer of your product arrives instrumented. Your telemetry never substitutes for
buyer-witnessed records — where both streams exist, the platform reconciles them and
prints discrepancies as findings (that reconciliation is what makes your sponsored
verification reports credible to your buyer's CFO, and later, what makes outcome
billing enforceable). Do not send buyer PII: user_ref is pseudonymous by design.
