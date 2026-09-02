'use client';

import * as React from 'react';

import { LIVE_REFRESH_MS, type LiveStatus } from '../../lib/api-client';

/**
 * Keep a page's data current without anyone asking.
 *
 * One hook, used by every data-driven page, so "live" means the same thing
 * everywhere and no page can invent its own refresh behaviour.
 *
 * Two properties matter:
 *
 * A failed poll does NOT clear what is on screen. Showing a merchant an empty
 * dashboard because one request timed out would be worse than showing them
 * figures that are five seconds stale — so the last good data stays, and the
 * status quietly becomes "Reconnecting…".
 *
 * Polling pauses when the tab is hidden. A backgrounded tab hammering the API
 * every five seconds for hours is waste nobody benefits from; it resumes, and
 * refetches immediately, when the tab comes back.
 */
export function useLiveData<T>(
  fetcher: () => Promise<T>,
  deps: React.DependencyList = [],
): {
  data: T | null;
  status: LiveStatus;
  lastUpdated: Date | null;
  loading: boolean;
  refresh: () => void;
} {
  const [data, setData] = React.useState<T | null>(null);
  const [status, setStatus] = React.useState<LiveStatus>('live');
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null);
  const [loading, setLoading] = React.useState(true);

  // Held in a ref so changing the fetcher identity does not restart the timer
  // on every render.
  const fetcherRef = React.useRef(fetcher);
  React.useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const load = React.useCallback(async () => {
    try {
      const next = await fetcherRef.current();
      setData(next);
      setStatus('live');
      setLastUpdated(new Date());
    } catch {
      // Deliberately keeps the previous data. Stale figures beat a blank page.
      setStatus('reconnecting');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const tick = () => {
      if (!cancelled && !document.hidden) void load();
    };

    void load();
    timer = setInterval(tick, LIVE_REFRESH_MS);

    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, ...deps]);

  return { data, status, lastUpdated, loading, refresh: () => void load() };
}

/**
 * Keep an existing page current, without restructuring its state.
 *
 * Pages already own their filters, pagination and loaders. Rewriting each to
 * hand that state to a hook would be a large change for no benefit, so this
 * takes the opposite approach: the page keeps its loader, and this owns only
 * the timing.
 *
 * The loader is called with ``quiet`` on background refreshes. A quiet refresh
 * must not show a spinner and must not clear what is on screen — blanking a
 * page every five seconds would make live updating worse than not having it.
 * It returns whether the fetch succeeded, which is what drives Live vs
 * Reconnecting; failures deliberately leave the previous data alone.
 *
 * Polling pauses while the tab is hidden and refetches the moment it returns,
 * so a backgrounded tab is not hitting the API every five seconds for hours.
 */
export function useLiveRefresh(
  load: (quiet: boolean) => Promise<boolean>,
  deps: React.DependencyList = [],
): { status: LiveStatus; lastUpdated: Date | null } {
  const [status, setStatus] = React.useState<LiveStatus>('live');
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null);

  // Held in a ref so a page redefining its loader each render does not restart
  // the timer on every render.
  const loadRef = React.useRef(load);
  React.useEffect(() => {
    loadRef.current = load;
  });

  React.useEffect(() => {
    let cancelled = false;

    const run = async (quiet: boolean) => {
      const ok = await loadRef.current(quiet);
      if (cancelled) return;
      setStatus(ok ? 'live' : 'reconnecting');
      if (ok) setLastUpdated(new Date());
    };

    void run(false);
    const timer = setInterval(() => {
      if (!document.hidden) void run(true);
    }, LIVE_REFRESH_MS);

    const onVisible = () => {
      if (!document.hidden) void run(true);
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { status, lastUpdated };
}
