# @truefigure/react

Thin React bindings — a Provider and hooks over [`@truefigure/sdk`](../node).
**UI ergonomics only; no logic of its own.** All measurement, identity, and
business rules stay on the server.

> **Browser security:** anything shipped to a browser is public. Use a **scoped,
> read-only API key** here. Dashboards read figures/usage/health; they should not
> carry a write/admin-scoped key.

## Install & build

```sh
cd sdk/react
npm install        # links @truefigure/sdk from ../node
npm run build      # tsc -> dist/
npm test           # build + node --test (headless: reducer + provider/context)
```

Peer dependencies: `react >= 17` and `@truefigure/sdk ^0.1.0`.

## Usage

```tsx
import {
  TrueFigureProvider, useLiveUsage, useFigures, useIntegrationHealth,
} from "@truefigure/react";

function Dashboard({ deploymentId }: { deploymentId: string }) {
  const usage  = useLiveUsage(deploymentId);
  const health = useIntegrationHealth(deploymentId);
  const figs   = useFigures(deploymentId, "2026-Q2");

  if (usage.loading) return <Spinner/>;
  if (usage.error)   return <ErrorBanner error={usage.error}/>;
  return <SeatWaste usage={usage.data} health={health.data} figures={figs.data}/>;
}

export default function App() {
  return (
    <TrueFigureProvider apiKey={import.meta.env.VITE_TF_READ_KEY} baseUrl="https://api.truefigure.io">
      <Dashboard deploymentId="dep_1"/>
    </TrueFigureProvider>
  );
}
```

Every read hook returns `{ data, error, loading, refetch }`. Superseded requests
(e.g. a changed `deploymentId`) are dropped so state never races.

## Hooks

- `useTrueFigure()` — the underlying thin client (escape hatch)
- Read: `useWhoami`, `useFigures`, `useFigure`, `useFigureLineage`,
  `useLiveUsage`, `useLiveCost`, `useIntegrationHealth`, `useReports`,
  `useReport`, `useAlerts`
- Write (use a scoped key): `useTrack()` → `{ track, ingest, flush }`

Pass `{ enabled: false }` to defer a query until inputs are ready.
