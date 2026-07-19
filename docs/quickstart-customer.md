# Quickstart: customer-system integration (origin=customer_system)
Goal: first event accepted and echoed in under 30 minutes.
1. Register the deployment: client.register_deployment("Claims drafting AI", "saas_tool", planned_rollout_at=...).
   Set planned_rollout_at if the AI is not yet live — pre-rollout baseline capture is the
   strongest evidence class you will ever have, and it starts the moment lifecycle events flow.
2. Register id namespaces if your user/work-item ids differ from your work system's.
3. Wire the four emit points: AI touch (activity), work transitions (lifecycle),
   consumption (cost_meter), QA outcomes (quality_signal).
4. Run in test mode; read every echo; fix namespace/timezone issues against live feedback.
5. Flip to production; watch /v1/live/health/{deployment_id} for accept/join/resolution rates.
6. Connect AI activity NOW even if you integrate the rest later: AI tool logs typically
   retain only ~90-180 days upstream — history you don't witness is history destroyed.
