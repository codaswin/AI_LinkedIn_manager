import type { ReactNode } from "react";
import { useState } from "react";
import {
  triggerAnalyticsWorkflow,
  triggerContentWorkflow,
  triggerEngagementWorkflow,
  triggerResearchWorkflow,
} from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { ResearchResultItem, WorkflowResult } from "../types";

const RESEARCH_SOURCES = ["hackernews", "reddit", "github", "producthunt", "rss", "web", "x"] as const;

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

// ---------------------------------------------------------------------------
// Lightweight, safe rendering of model-generated post copy: bold spans and
// hashtags styled, "- " lines grouped into a real list, paragraphs kept as
// paragraphs. Not a general markdown parser — post_content only ever uses
// this much formatting (see content_writer.py's prompt) — so it renders the
// actual shape of the text instead of a raw string dump.
// ---------------------------------------------------------------------------

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|#\w+)/g).filter(Boolean);
  return parts.map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("#")) {
      return (
        <span className="hashtag" key={key}>
          {part}
        </span>
      );
    }
    return part;
  });
}

function PostContentBlock({ block, index }: { block: string; index: number }) {
  const blockLines = block.split("\n").map((l) => l.trim()).filter(Boolean);
  const isList = blockLines.length > 1 && blockLines.every((l) => l.startsWith("- "));

  if (isList) {
    return (
      <ul className="post-list" key={index}>
        {blockLines.map((l, i) => (
          <li key={i}>{renderInline(l.replace(/^- /, ""), `${index}-${i}`)}</li>
        ))}
      </ul>
    );
  }

  return (
    <p key={index}>
      {blockLines.map((l, i) => (
        <span key={i}>
          {renderInline(l, `${index}-${i}`)}
          {i < blockLines.length - 1 && <br />}
        </span>
      ))}
    </p>
  );
}

function PostContent({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className="post-content">
      {blocks.map((block, i) => (
        <PostContentBlock block={block} index={i} key={i} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-workflow result panels — each reads the exact shape its backend
// endpoint returns (see backend/app/main.py's workflow trigger handlers)
// rather than dumping the raw response as a generic key/value list.
// ---------------------------------------------------------------------------

function ContentResult({ result }: { result: WorkflowResult }) {
  const needsRewrite = Boolean(result.needs_human_rewrite);
  const confidence = num(result.confidence);
  const topic = str(result.topic);
  const postContent = str(result.post_content);
  const toolName = str(result.tool_name);
  const brief = (result.brief ?? {}) as Record<string, unknown>;
  const format = str(brief.format);
  const targetDate = str(brief.target_publish_date);

  return (
    <div className="result-panel">
      <div className="result-head">
        <h4>{topic ?? "Untitled post"}</h4>
        <span className={needsRewrite ? "badge badge-rejected" : "badge badge-approved"}>
          {needsRewrite ? "Needs human rewrite" : "Submitted for approval"}
        </span>
      </div>
      <p className="result-meta">
        {format && (
          <>
            Format <strong>{format}</strong>
          </>
        )}
        {targetDate && (
          <>
            {" · "}Target <strong>{targetDate}</strong>
          </>
        )}
        {confidence !== null && (
          <>
            {" · "}Confidence <strong>{Math.round(confidence * 100)}%</strong>
          </>
        )}
        {toolName && (
          <>
            {" · "}
            <span className="mono-chip">{toolName}</span>
          </>
        )}
      </p>
      {postContent && (
        <div className="post-content-card">
          <PostContent text={postContent} />
        </div>
      )}
      <p className="result-footnote">
        {needsRewrite
          ? "Confidence came in below the 0.75 threshold — this draft was never queued. Rewrite it yourself or trigger the workflow again."
          : "Waiting in the Approval Queue — nothing publishes until a human approves it."}
      </p>
    </div>
  );
}

function AnalyticsResult({ result }: { result: WorkflowResult }) {
  const totalImpressions = num(result.total_impressions);
  const engagementRate = num(result.avg_engagement_rate);
  const followerDelta = num(result.follower_delta);
  const flagged = Array.isArray(result.flagged_posts) ? (result.flagged_posts as Record<string, string>[]) : [];

  return (
    <div className="result-panel">
      <div className="stat-row">
        <div className="stat-tile">
          <span className="stat-n">{totalImpressions ?? "—"}</span>
          <span className="stat-l">Impressions</span>
        </div>
        <div className="stat-tile">
          <span className="stat-n">{engagementRate !== null ? `${(engagementRate * 100).toFixed(1)}%` : "—"}</span>
          <span className="stat-l">Avg. engagement</span>
        </div>
        <div className="stat-tile">
          <span className="stat-n">
            {followerDelta !== null ? (followerDelta >= 0 ? `+${followerDelta}` : followerDelta) : "—"}
          </span>
          <span className="stat-l">Follower delta</span>
        </div>
      </div>
      {flagged.length === 0 ? (
        <p className="result-footnote">No posts flagged this period.</p>
      ) : (
        <ul className="flag-list">
          {flagged.map((f, i) => (
            <li key={i}>
              <span className="badge badge-rejected">Flagged</span> <strong>{f.post_id}</strong>
              {f.reason && <> — {f.reason}</>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const REPLY_TEXT_KEYS = ["reply_text", "message_text", "note"] as const;

function EngagementResult({ result }: { result: WorkflowResult }) {
  const status = str(result.status);
  const escalated = status === "escalated";
  const confidence = num(result.confidence);
  const approvalRequest = (result.approval_request ?? null) as Record<string, unknown> | null;
  const args = (approvalRequest?.arguments ?? {}) as Record<string, unknown>;
  const replyKey = REPLY_TEXT_KEYS.find((key) => typeof args[key] === "string");
  const replyText = replyKey ? (args[replyKey] as string) : null;
  const toolName = str(approvalRequest?.tool_name) ?? str(result.tool_name);
  const reason = str(result.reason);
  const topic = str(result.topic);

  return (
    <div className="result-panel">
      <div className="result-head">
        <h4>{escalated ? "Escalated to a human" : "Reply drafted"}</h4>
        <span className={escalated ? "badge badge-rejected" : "badge badge-approved"}>
          {escalated ? "No draft produced" : "Submitted for approval"}
        </span>
      </div>
      <p className="result-meta">
        {confidence !== null && (
          <>
            Confidence <strong>{Math.round(confidence * 100)}%</strong>
          </>
        )}
        {toolName && (
          <>
            {" · "}
            <span className="mono-chip">{toolName}</span>
          </>
        )}
      </p>
      {escalated ? (
        <p className="result-footnote">
          {reason === "refusal_topic" && topic
            ? `Matched a refusal-topic guardrail ("${topic}") — routed straight to a human, no draft attempted.`
            : reason === "low_confidence"
              ? "Draft confidence came in below the 0.75 threshold — escalated instead of queued."
              : "Escalated to a human."}
        </p>
      ) : (
        <>
          {replyText && (
            <div className="post-content-card">
              <PostContent text={replyText} />
            </div>
          )}
          <p className="result-footnote">Waiting in the Approval Queue — nothing sends until a human approves it.</p>
        </>
      )}
    </div>
  );
}

// Each card below triggers the same agent entrypoint the scheduler calls —
// see backend/app/main.py's "Manual workflow triggers" section. Triggering
// one makes app.activity's board go active, which is what drives
// ActivityBanner's animation; this view just fires the request and shows
// whatever the workflow returned.
function ResearchTrigger() {
  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<string[]>([...RESEARCH_SOURCES].filter((s) => s !== "x"));
  const [limitPerSource, setLimitPerSource] = useState(10);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ResearchResultItem[] | null>(null);

  function toggleSource(source: string) {
    setSources((prev) => (prev.includes(source) ? prev.filter((s) => s !== source) : [...prev, source]));
  }

  async function run() {
    if (!query.trim()) return;
    setRunning(true);
    setError(null);
    setResults(null);
    try {
      const result = await triggerResearchWorkflow(query.trim(), sources.length ? sources : null, limitPerSource);
      setResults(Array.isArray(result.results) ? (result.results as ResearchResultItem[]) : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <article className="card">
      <h3>Research</h3>
      <p className="card-meta">Fetches trending sources concurrently, dedupes, and ranks — no LLM call required.</p>
      <ErrorBanner message={error} />
      <div className="card-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
        <input type="text" placeholder="Topic to research, e.g. AI agents" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="source-checkboxes">
          {RESEARCH_SOURCES.map((source) => (
            <label key={source} className="source-checkbox">
              <input type="checkbox" checked={sources.includes(source)} onChange={() => toggleSource(source)} />
              {source}
            </label>
          ))}
        </div>
        <label className="actor-field">
          Results per source
          <input
            type="text"
            inputMode="numeric"
            value={limitPerSource}
            onChange={(e) => setLimitPerSource(Number(e.target.value.replace(/\D/g, "")) || 1)}
            style={{ width: "4rem" }}
          />
        </label>
        <button type="button" onClick={() => void run()} disabled={running || !query.trim()}>
          {running ? "Researching…" : "Run research"}
        </button>
      </div>

      {results && (
        <div className="result-panel">
          {results.length === 0 ? (
            <p className="result-footnote">No results came back.</p>
          ) : (
            <ul className="research-list">
              {results.map((item, idx) => (
                <li key={`${item.source}-${idx}`}>
                  <span className="source-chip">{item.source}</span>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.title}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </article>
  );
}

function ContentTrigger() {
  const [calendarEntries, setCalendarEntries] = useState("");
  const [recentTopics, setRecentTopics] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkflowResult | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await triggerContentWorkflow(lines(calendarEntries), lines(recentTopics)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <article className="card">
      <h3>Content (Strategist → Writer)</h3>
      <p className="card-meta">Chains a fresh brief straight into a draft — lands in the approval queue or flags needs_human_rewrite.</p>
      <ErrorBanner message={error} />
      <div className="card-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
        <textarea
          placeholder="Content calendar entries, one per line (optional)"
          value={calendarEntries}
          onChange={(e) => setCalendarEntries(e.target.value)}
          rows={2}
        />
        <textarea
          placeholder="Recent post topics to avoid repeating, one per line (optional)"
          value={recentTopics}
          onChange={(e) => setRecentTopics(e.target.value)}
          rows={2}
        />
        <button type="button" onClick={() => void run()} disabled={running}>
          {running ? "Generating…" : "Generate post"}
        </button>
      </div>
      {result && <ContentResult result={result} />}
    </article>
  );
}

function AnalyticsTrigger() {
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkflowResult | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await triggerAnalyticsWorkflow(periodStart || undefined, periodEnd || undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <article className="card">
      <h3>Analytics digest</h3>
      <p className="card-meta">Defaults to the last 7 days if no dates are given.</p>
      <ErrorBanner message={error} />
      <div className="card-actions">
        <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
        <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
        <button type="button" onClick={() => void run()} disabled={running}>
          {running ? "Analyzing…" : "Run digest"}
        </button>
      </div>
      {result && <AnalyticsResult result={result} />}
    </article>
  );
}

function EngagementTrigger() {
  const [notificationType, setNotificationType] = useState<"comment" | "dm" | "connection_request">("comment");
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkflowResult | null>(null);

  async function run() {
    if (!text.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await triggerEngagementWorkflow(notificationType, text.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <article className="card">
      <h3>Engagement reply</h3>
      <p className="card-meta">Triages a notification and drafts a reply — same path as an incoming comment/DM/connection request.</p>
      <ErrorBanner message={error} />
      <div className="card-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
        <select value={notificationType} onChange={(e) => setNotificationType(e.target.value as typeof notificationType)}>
          <option value="comment">Comment</option>
          <option value="dm">DM</option>
          <option value="connection_request">Connection request</option>
        </select>
        <textarea placeholder="Notification text" value={text} onChange={(e) => setText(e.target.value)} rows={3} />
        <button type="button" onClick={() => void run()} disabled={running || !text.trim()}>
          {running ? "Drafting…" : "Handle notification"}
        </button>
      </div>
      {result && <EngagementResult result={result} />}
    </article>
  );
}

export function WorkflowsView() {
  return (
    <section>
      <div className="view-header">
        <h2>Manual Workflow Triggers</h2>
      </div>
      <p className="empty-state">
        Fires the same agent entrypoints the scheduler calls automatically — watch the activity strip above for
        live progress while one runs.
      </p>
      <div className="card-list">
        <ResearchTrigger />
        <ContentTrigger />
        <AnalyticsTrigger />
        <EngagementTrigger />
      </div>
    </section>
  );
}
