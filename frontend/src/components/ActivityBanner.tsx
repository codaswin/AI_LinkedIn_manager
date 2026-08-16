import { useEffect, useRef, useState, type ComponentType } from "react";
import { BarChart3, BrainCircuit, Compass, MessageCircle, PenLine, Search, Settings2 } from "lucide-react";
import { motion } from "motion/react";
import { getActivity } from "../api";
import type { AgentActivity } from "../types";

// Blur+scale settling in together (not a plain fade) so a new agent taking
// over the banner reads as a real material arriving, per the design skill's
// "materialize, don't just fade" rule. Keyed by agent identity below (not
// the whole entry) so a poll tick that only updates elapsed-seconds doesn't
// re-trigger this on every refresh.
const MATERIALIZE = {
  initial: { opacity: 0, scale: 0.98, filter: "blur(3px)" },
  animate: { opacity: 1, scale: 1, filter: "blur(0px)" },
  transition: { type: "spring", stiffness: 420, damping: 38 } as const,
};

const POLL_INTERVAL_MS = 1200;

interface AgentMeta {
  icon: ComponentType<{ size?: number }>;
  label: string;
  animClass: string;
}

const AGENT_META: Record<string, AgentMeta> = {
  research: { icon: Search, label: "Research Agent", animClass: "activity-anim-scan" },
  content_strategist: { icon: Compass, label: "Content Strategist", animClass: "activity-anim-spin" },
  content_writer: { icon: PenLine, label: "Content Writer", animClass: "activity-anim-wiggle" },
  analytics: { icon: BarChart3, label: "Analytics & Reporting", animClass: "activity-anim-bars" },
  engagement: { icon: MessageCircle, label: "Engagement Agent", animClass: "activity-anim-bounce" },
  learning: { icon: BrainCircuit, label: "Self-Learning", animClass: "activity-anim-glow" },
};

const FALLBACK_META: AgentMeta = { icon: Settings2, label: "Agent", animClass: "activity-anim-spin" };

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
        // Background status is best effort and retries on the next poll.
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
    return <motion.div key="idle" className="activity-banner activity-banner-idle" {...MATERIALIZE}>
      <span className="activity-dot" />
      <span className="activity-text">All quiet - no workflow running right now</span>
    </motion.div>;
  }

  const meta = AGENT_META[entry.agent] ?? { ...FALLBACK_META, label: entry.agent };
  const AgentIcon = meta.icon;

  return <motion.div key={entry.agent} className="activity-banner activity-banner-active" {...MATERIALIZE}>
    <span className={`activity-icon ${meta.animClass}`} aria-hidden="true"><AgentIcon size={16} /></span>
    <span className="activity-text"><strong>{meta.label}</strong>{" - "}{entry.detail || entry.action}</span>
    <span className="activity-elapsed">{entry.elapsed_seconds.toFixed(1)}s</span>
  </motion.div>;
}
