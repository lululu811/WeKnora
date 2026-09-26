import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

interface Pick {
  ticker: string;
  exchange: string;
}

interface PicksResponse {
  code: number;
  data: Pick[];
}

/**
 * PicksBanner — Top-of-page strip showing stocks pushed by WeKnora's
 * `kline_studio.show` agent tool. Polls /api/picks every 5s so the banner
 * stays in sync with whatever the agent last pushed.
 *
 * Clicking a chip navigates to that stock's K-line page (/k/:ticker/:exchange).
 * When the user is already viewing one of the picks, that chip is highlighted.
 */
export function PicksBanner() {
  const { ticker: activeTicker, exchange: activeExchange } = useParams<{
    ticker: string;
    exchange: string;
  }>();
  const [picks, setPicks] = useState<Pick[]>([]);
  const [loading, setLoading] = useState(true);
  const lastErrorRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const resp = await fetch('/api/picks');
        const json: PicksResponse = await resp.json();
        if (cancelled) return;
        if (json?.code === 0 && Array.isArray(json.data)) {
          setPicks(json.data);
          lastErrorRef.current = null;
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (message !== lastErrorRef.current) {
          // throttle identical errors so the console stays readable
          console.warn('[PicksBanner] failed to load picks:', err);
          lastErrorRef.current = message;
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // No picks pushed yet — keep the layout stable but show a quiet hint.
  if (!loading && picks.length === 0) {
    return (
      <div className="picks-banner picks-banner--empty">
        <span className="picks-banner-label">今日 picks</span>
        <span className="picks-banner-hint">等待 WeKnora agent 推送…</span>
      </div>
    );
  }

  return (
    <div className="picks-banner" role="region" aria-label="今日 picks">
      <span className="picks-banner-label">今日 picks</span>
      <ul className="picks-banner-list">
        {picks.map((pick, idx) => {
          const isActive =
            activeTicker === pick.ticker && activeExchange === pick.exchange;
          return (
            <li key={`${pick.ticker}-${pick.exchange}-${idx}`}>
              <Link
                to={`/k/${pick.ticker}/${pick.exchange}`}
                className={`picks-chip${isActive ? ' picks-chip--active' : ''}`}
                title={`${pick.ticker}.${pick.exchange}`}
              >
                <span className="picks-chip-code">{pick.ticker}</span>
                <span className="picks-chip-exchange">{pick.exchange}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}