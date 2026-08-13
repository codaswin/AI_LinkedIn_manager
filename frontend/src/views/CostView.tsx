import { useCallback, useEffect, useState } from "react";
import { getCostSummary } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { CostSummary } from "../types";

export function CostView() {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await getCostSummary());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pct = summary && summary.budget_usd > 0 ? Math.min((summary.today_usd / summary.budget_usd) * 100, 100) : 0;
  const overBudget = summary ? summary.today_usd >= summary.budget_usd : false;

  return (
    <section>
      <div className="view-header">
        <h2>LLM Cost — Today</h2>
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <ErrorBanner message={error} />

      {summary && (
        <article className="card">
          <p className="cost-figure">
            ${summary.today_usd.toFixed(4)} <span className="cost-of">of ${summary.budget_usd.toFixed(2)} daily cap</span>
          </p>
          <div className="cost-bar-track">
            <div
              className={overBudget ? "cost-bar-fill cost-bar-over" : "cost-bar-fill"}
              style={{ width: `${pct}%` }}
            />
          </div>
          {overBudget && (
            <p className="card-flag">
              Daily budget reached — llmops.cost_tracker.check_budget() will refuse further LLM calls until the UTC
              day rolls over.
            </p>
          )}
        </article>
      )}
    </section>
  );
}
