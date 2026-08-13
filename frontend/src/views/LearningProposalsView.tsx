import { useCallback, useEffect, useState } from "react";
import { approveLearningProposal, listLearningProposals, rejectLearningProposal, triggerReflection } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { StatusBadge } from "../components/StatusBadge";
import { useActor } from "../actorStore";
import type { LearningProposal } from "../types";

// Mirrors app/learning/proposal_review.py exactly — these types NEVER
// auto-apply regardless of confidence, so the UI should never suggest
// otherwise (e.g. no "auto-apply" affordance is offered for them here).
const ALWAYS_REVIEW_TYPES = new Set([
  "system_prompt",
  "brand_voice_profile",
  "new_tool",
  "approval_gating_rule",
  "confidence_threshold",
  "safety_threshold",
]);

export function LearningProposalsView() {
  const { actor } = useActor();
  const [proposals, setProposals] = useState<LearningProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reflecting, setReflecting] = useState(false);
  const [lastReflection, setLastReflection] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProposals(await listLearningProposals());
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
      await approveLearningProposal(id, actor);
      await refresh();
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
      await rejectLearningProposal(id, actor);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReflectNow() {
    setReflecting(true);
    setError(null);
    setLastReflection(null);
    try {
      const result = await triggerReflection(7);
      setLastReflection(
        result.ran
          ? `Ran: ${result.feedback_count} feedback entries analyzed, ${result.proposal_count ?? 0} proposal(s) submitted.`
          : `Skipped: ${result.reason} (${result.feedback_count} feedback entries — needs at least 5).`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReflecting(false);
    }
  }

  return (
    <section>
      <div className="view-header">
        <h2>Self-Learning Review Queue</h2>
        <div className="header-actions">
          <button type="button" onClick={() => void handleReflectNow()} disabled={reflecting}>
            {reflecting ? "Reflecting…" : "Run reflection now"}
          </button>
          <button type="button" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>
      <ErrorBanner message={error} />
      {lastReflection && <p className="info-banner">{lastReflection}</p>}

      {!loading && proposals.length === 0 && (
        <p className="empty-state">
          No proposals awaiting review. The reflection job runs weekly by default (also triggerable on demand above)
          and only submits proposals after at least 5 recent negative-feedback entries.
        </p>
      )}

      <div className="card-list">
        {proposals.map((proposal) => (
          <article className="card" key={proposal.id}>
            <div className="card-title-row">
              <h3>{proposal.change_type.replace(/_/g, " ")}</h3>
              <StatusBadge status={proposal.status} />
            </div>
            {ALWAYS_REVIEW_TYPES.has(proposal.change_type) && (
              <p className="card-flag">Always requires human review — never auto-applies, regardless of confidence.</p>
            )}
            <p className="card-meta">
              Confidence {proposal.confidence.toFixed(2)} · {new Date(proposal.created_at).toLocaleString()}
            </p>
            <p className="card-reason">
              <strong>Pattern:</strong> {proposal.pattern}
            </p>
            <p className="card-reason">
              <strong>Proposed change:</strong> {proposal.proposed_change}
            </p>

            {proposal.status === "pending" && (
              <div className="card-actions">
                <button
                  type="button"
                  className="btn-approve"
                  disabled={busyId === proposal.id}
                  onClick={() => void handleApprove(proposal.id)}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="btn-reject"
                  disabled={busyId === proposal.id}
                  onClick={() => void handleReject(proposal.id)}
                >
                  Reject
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
