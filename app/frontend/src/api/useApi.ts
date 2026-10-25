import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "./client";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export interface UseApi<T> {
  state: AsyncState<T>;
  /** Re-run the loader (shows loading state again). */
  reload: () => void;
}

export interface UseApiOptions {
  /** Re-run `load` when this string changes (e.g. a selected date). Omit for a mount-only load. */
  key?: string;
  /** Turn a thrown value into the user-facing message. Defaults to describeError. */
  describe?: (err: unknown) => string;
}

/**
 * Run `load` on mount, whenever `options.key` changes, and whenever `reload()` is called.
 * Stale responses are dropped. `load` is read through a ref so callers may pass an inline arrow.
 */
export function useApi<T>(load: () => Promise<T>, options: UseApiOptions = {}): UseApi<T> {
  const { key = "", describe = describeError } = options;
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const describeRef = useRef(describe);
  describeRef.current = describe;

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    loadRef.current().then(
      (data) => {
        if (live) setState({ status: "ready", data });
      },
      (err: unknown) => {
        if (live) setState({ status: "error", message: describeRef.current(err) });
      },
    );
    return () => {
      live = false;
    };
  }, [tick, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { state, reload };
}
