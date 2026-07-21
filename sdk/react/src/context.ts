/**
 * React context carrying a single thin TrueFigureClient. No proprietary logic
 * lives in this package — it is a UI-ergonomics layer over @truefigure/sdk.
 */
import { createContext } from "react";
import type { TrueFigureClient } from "@truefigure/sdk/browser";

export const TrueFigureContext = createContext<TrueFigureClient | null>(null);
TrueFigureContext.displayName = "TrueFigureContext";
