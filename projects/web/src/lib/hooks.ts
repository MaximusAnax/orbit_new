/**
 * Data-fetching primitives.
 *
 * Every screen must handle loading, empty and error — an app that shows a blank
 * panel when a request fails is the UI equivalent of a swallowed exception, so
 * `useQuery` returns all three states explicitly rather than letting a screen
 * render `undefined` and look fine.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type QueryState<T> = {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  /** True on the very first load, so lists can show a skeleton only once. */
  initial: boolean;
  reload: () => void;
};

export function useQuery<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  options: { enabled?: boolean } = {},
): QueryState<T> {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [loading, setLoading] = useState(enabled);
  const [initial, setInitial] = useState(true);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let current = true;
    setLoading(true);
    fetcher()
      .then((value) => {
        if (current && alive.current) {
          setData(value);
          setError(undefined);
        }
      })
      .catch((err: unknown) => {
        if (current && alive.current) {
          setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
        }
      })
      .finally(() => {
        if (current && alive.current) {
          setLoading(false);
          setInitial(false);
        }
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, initial, reload };
}

export type MutationState<A extends unknown[], T> = {
  run: (...args: A) => Promise<T | undefined>;
  data: T | undefined;
  error: ApiError | undefined;
  pending: boolean;
  reset: () => void;
};

export function useMutation<A extends unknown[], T>(
  action: (...args: A) => Promise<T>,
): MutationState<A, T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [pending, setPending] = useState(false);

  const run = useCallback(
    async (...args: A) => {
      setPending(true);
      setError(undefined);
      try {
        const value = await action(...args);
        setData(value);
        return value;
      } catch (err: unknown) {
        setError(err instanceof ApiError ? err : new ApiError(0, "unknown", String(err)));
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [action],
  );

  const reset = useCallback(() => {
    setData(undefined);
    setError(undefined);
  }, []);

  return { run, data, error, pending, reset };
}

/** Persisted state, used for the theme and the last-opened project. */
export function useStored<T>(key: string, fallback: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
      return fallback;
    }
  });
  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* private mode — in-memory only */
      }
    },
    [key],
  );
  return [value, set];
}
