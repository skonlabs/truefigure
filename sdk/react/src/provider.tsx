/**
 * <TrueFigureProvider> — supplies a thin TrueFigureClient to the React tree.
 *
 * SECURITY NOTE: anything shipped to a browser is public. Use a scoped, read-
 * oriented API key here (dashboards read figures/usage/health); never embed a
 * key with write/admin scope in client-side code. The provider adds no logic
 * beyond constructing/holding the client.
 */
import { useMemo, type ReactNode } from "react";
import { TrueFigureClient, type ClientOptions } from "@truefigure/sdk/browser";
import { TrueFigureContext } from "./context.js";

export interface TrueFigureProviderProps extends ClientOptions {
  /** Scoped API key (prefer a read-only token in the browser). */
  apiKey: string;
  /** Or supply a pre-built client instead of apiKey/options. */
  client?: TrueFigureClient;
  children: ReactNode;
}

export function TrueFigureProvider({ apiKey, client, children, ...options }: TrueFigureProviderProps): JSX.Element {
  const value = useMemo(
    () => client ?? new TrueFigureClient(apiKey, options),
    // Re-create only when the identity-bearing inputs change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, apiKey, options.baseUrl, options.deploymentId, options.sourceRef],
  );
  return <TrueFigureContext.Provider value={value}>{children}</TrueFigureContext.Provider>;
}
