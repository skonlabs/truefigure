/**
 * Generic async-resource state for the read hooks. The data-fetching itself is
 * just a thin @truefigure/sdk client call; this only manages loading/error/data
 * and a refetch handle. The reducer is exported for headless testing.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { TrueFigureError } from "@truefigure/sdk/browser";

export interface AsyncState<T> {
  data: T | null;
  error: TrueFigureError | Error | null;
  loading: boolean;
  /** Re-run the fetch. */
  refetch: () => void;
}

type InternalState<T> = { data: T | null; error: Error | null; loading: boolean };

export type AsyncAction<T> =
  | { type: "start" }
  | { type: "success"; data: T }
  | { type: "failure"; error: Error };

export function asyncReducer<T>(state: InternalState<T>, action: AsyncAction<T>): InternalState<T> {
  switch (action.type) {
    case "start":
      return { ...state, loading: true, error: null };
    case "success":
      return { data: action.data, error: null, loading: false };
    case "failure":
      return { data: null, error: action.error, loading: false };
    default:
      return state;
  }
}

export function initialAsyncState<T>(): InternalState<T> {
  return { data: null, error: null, loading: true };
}

/**
 * Run `fetcher` on mount and whenever `deps` change; `enabled=false` skips it.
 * Stale responses (from superseded runs) are dropped.
 */
export function useAsyncResource<T>(
  fetcher: () => Promise<T>,
  deps: ReadonlyArray<unknown>,
  enabled = true,
): AsyncState<T> {
  const [state, dispatch] = useReducer(asyncReducer<T>, initialAsyncState<T>());
  const runId = useRef(0);

  const run = useCallback(() => {
    if (!enabled) return;
    const myRun = ++runId.current;
    dispatch({ type: "start" });
    fetcher().then(
      (data) => { if (myRun === runId.current) dispatch({ type: "success", data }); },
      (error: unknown) => {
        if (myRun === runId.current) {
          dispatch({ type: "failure", error: error instanceof Error ? error : new Error(String(error)) });
        }
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  useEffect(() => {
    run();
    return () => { runId.current++; }; // invalidate in-flight on unmount/dep-change
  }, [run]);

  return { data: state.data, error: state.error, loading: enabled ? state.loading : false, refetch: run };
}
