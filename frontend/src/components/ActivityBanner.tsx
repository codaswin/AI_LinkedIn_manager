import { useEffect, useRef, useState } from "react";
import { getActivity } from "../api";
import type { AgentActivity } from "../types";

const POLL_INTERVAL_MS = 1200;

const SOURCE_ICONS: Record<string, string> = {
  hackernews: "📰",
  reddit: "👽",
  github: "🐙",
  producthunt: "🚀",
  rss: "📡",
  web: "🌐",
  x: "🐦",
};

interface AgentMeta {
  icon: string;
  label: string;
  animClass: string;
}

const AGENT_META: Record<string, AgentMeta> = {
  research: { icon: "🔍", label: "Research Agent", animClass: "activity-anim-scan" },
  content_strategist: { icon: "🧭", label: "Content Strategist", animClass: "activity-anim-spin" },
  content_writer: { icon: "✍️", label: "Content Writer", animClass: "activity-anim-wiggle" },
  analytics: { icon: "📊", label: "Analytics & Reporting", animClass: "activity-anim-bars" },
  engagement: { icon: "💬", label: "Engagement Agent", animClass: "activity-anim-bounce" },
  learning: { icon: "🧠", label: "Self-Learning", animClass: "activity-anim-glow" },
};

const FALLBACK_META: AgentMeta = { icon: "⚙️", label: "Agent", animClass: "activity-anim-spin" };

function iconFor(entry: AgentActivity): string {
  if (entry.agent === "research" && entry.source && SOURCE_ICONS[entry.source]) {
    return SOURCE_ICONS[entry.source];
  }
  return (AGENT_META[entry.agent] ?? FALLBACK_META).icon;
}

// Polls GET /activity on an interval and renders a small graphic that reacts
// to whatever the system is doing right now (per app.activity's stack-based
// "what's active" board) — a different icon/animation per agent, and per
// research source, so "searching Reddit" visibly looks different from
// "searching GitHub" or "drafting a reply." Mounted once, persistently,
// outside the tab views so it stays visible no matter which tab is open.
export function ActivityBanner() {
  const [entry, setEntry] = useState<AgentActivity | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await getActivity();
        if (!cancelled) setEntry(result);
      } catch {
        // Best-effort background chrome — a transient poll failure shouldn't
        // surface an error banner anywhere; just retry on the next tick.
      }
    };
    void poll();
    timerRef.current = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, []);

  if (!entry) {
    return (
      <div className="activity-banner activity-banner-idle">
        <span className="activity-dot" />
        <span className="activity-text">All quiet — no workflow running right now</span>
      </div>
    );
  }

  const meta = AGENT_META[entry.agent] ?? { ...FALLBACK_META, label: entry.agent };

  return (
    <div className="activity-banner activity-banner-active">
      <span className={`activity-icon ${meta.animClass}`} aria-hidden="true">
        {iconFor(entry)}
      </span>
      <span className="activity-text">
        <strong>{meta.label}</strong>
        {" — "}
        {entry.detail || entry.action}
      </span>
      <span className="activity-elapsed">{entry.elapsed_seconds.toFixed(1)}s</span>
    </div>
  );
}
