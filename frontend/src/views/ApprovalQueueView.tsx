import { useCallback, useEffect, useState } from "react";
import { approveApproval, listApprovals, rejectApproval } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { StatusBadge } from "../components/StatusBadge";
import { useActor } from "../actorStore";
import type { ApprovalRequest } from "../types";

function ArgumentsPreview({ args }: { args: Record<string, unknown> }) {
  // Renders every argument key/value so nothing is hidden from the human
  // approving it — project rules: a delete_post approval must show the full
  // post content, not a bare ID. Applies the same "show everything" rule
  // to every gated tool, not just delete_post specifically.
  return (
    <dl className="arg-list">
      {Object.entries(args).map(([key, value]) => (
        <div className="arg-row" key={key}>
          <dt>{key}</dt>
          <dd>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ApprovalQueueView() {
  const { actor } = useActor();
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reasonDrafts, setReasonDrafts] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setApprovals(await listApprovals());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleApprove(id: string) {
    setBusyId(id);
    setError(null);
    try {
      // A 200 response here does not mean the action actually happened —
      // /approvals/{id}/approve marks the request approved AND immediately
      // executes the underlying tool (e.g. actually posting to LinkedIn),
      // returning that execution's own result. A failed post (bad
      // credentials, Composio down, ...) still comes back as a normal 200
      // with status: "error" inside the body — approving is only "done"
      // once that inner status is checked too.
      const result = await approveApproval(id, actor);
      // refresh() clears the error banner itself (it has its own fetch that
      // can fail) — it must run BEFORE this function sets its own error, or
      // refresh()'s unconditional setError(null) wipes this one out before
      // it's ever seen.
      await refresh();
      if (result.status !== "success") {
        setError(
          `Approved, but the action itself failed (${result.status}): ${result.error ?? "no error detail returned"}`,
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await rejectApproval(id, actor, reasonDrafts[id]);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section>
      <div className="view-header">
        <h2>Approval Queue</h2>
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <ErrorBanner message={error} />

      {!loading && approvals.length === 0 && (
        <p className="empty-state">Nothing pending — every gated action is waiting on a human, and right now none are.</p>
      )}

      <div className="card-list">
        {approvals.map((approval) => (
          <article className="card" key={approval.id}>
            <div className="card-title-row">
              <h3>{approval.tool_name}</h3>
              <StatusBadge status={approval.status} />
            </div>
            <p className="card-meta">
              Requested by <strong>{approval.requested_by_agent}</strong>
              {approval.confidence !== null && <> · confidence {approval.confidence.toFixed(2)}</>}
              {" · "}
              {new Date(approval.created_at).toLocaleString()}
            </p>
            <p className="card-reason">{approval.reason}</p>
            <ArgumentsPreview args={approval.arguments} />

            <div className="card-actions">
              <input
                type="text"
                placeholder="Rejection reason (optional)"
                value={reasonDrafts[approval.id] ?? ""}
                onChange={(e) => setReasonDrafts((prev) => ({ ...prev, [approval.id]: e.target.value }))}
              />
              <button
                type="button"
                className="btn-approve"
                disabled={busyId === approval.id}
                onClick={() => void handleApprove(approval.id)}
              >
                Approve
              </button>
              <button
                type="button"
                className="btn-reject"
                disabled={busyId === approval.id}
                onClick={() => void handleReject(approval.id)}
              >
                Reject
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
