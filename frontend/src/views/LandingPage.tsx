import { type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import {
  ArrowRight,
  BarChart3,
  Compass,
  History,
  LockKeyhole,
  MessageCircle,
  PenLine,
  Search,
  ShieldCheck,
} from "lucide-react";
import { motion, useMotionValue, useSpring } from "motion/react";
import "../LandingPage.css";

/** A five-node network mark — one glowing badge standing in for "one LinkedIn
 * presence, five agents," styled like a rounded-square app icon (the same
 * badge convention LinkedIn's own mark uses) but its own abstract glyph, not
 * a triangle and not a lettermark. */
function NetworkMark() {
  const nodes = [
    [32, 8],
    [54, 22],
    [46, 48],
    [18, 48],
    [10, 22],
  ] as const;
  return (
    <svg viewBox="0 0 64 64" width="40" height="40" aria-hidden="true">
      <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity="0.55">
        {nodes.map(([x, y], i) => {
          const [nx, ny] = nodes[(i + 1) % nodes.length];
          return <line key={i} x1={x} y1={y} x2={nx} y2={ny} />;
        })}
        <line x1="32" y1="30" x2="32" y2="8" />
        <line x1="32" y1="30" x2="54" y2="22" />
        <line x1="32" y1="30" x2="46" y2="48" />
        <line x1="32" y1="30" x2="18" y2="48" />
        <line x1="32" y1="30" x2="10" y2="22" />
      </g>
      <circle cx="32" cy="30" r="5" fill="currentColor" />
      {nodes.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="3.2" fill="currentColor" />
      ))}
    </svg>
  );
}

const PIPELINE = [
  { icon: Search, label: "Research", detail: "Scans trending signals" },
  { icon: Compass, label: "Strategist", detail: "Picks topic & angle" },
  { icon: PenLine, label: "Writer", detail: "Drafts the post" },
  { icon: ShieldCheck, label: "You", detail: "Approve or reject" },
] as const;

const AGENTS = [
  {
    icon: Search,
    name: "Research Agent",
    copy:
      "Pulls trending signals from Hacker News, Reddit, GitHub, Product Hunt, RSS, and the open web — concurrently, deduped, and ranked, no LLM call required.",
  },
  {
    icon: Compass,
    name: "Content Strategist",
    copy:
      "Reads your last 30 days of posts and the content calendar, then picks one topic, angle, and format nothing's already covered.",
  },
  {
    icon: PenLine,
    name: "Content Writer",
    copy:
      "Drafts in your brand voice, grounded in real research context — and refuses to invent statistics or case studies it can't back up.",
  },
  {
    icon: MessageCircle,
    name: "Engagement Agent",
    copy:
      "Triages incoming comments, DMs, and connection requests, and drafts a reply for every one — never sent without your say-so.",
  },
  {
    icon: BarChart3,
    name: "Analytics & Reporting",
    copy: "Turns raw post performance into a digest you can actually act on, on the schedule you set.",
  },
] as const;

const TRUST = [
  {
    icon: ShieldCheck,
    title: "A human approves every publish",
    copy: "Nothing posts, deletes, or messages anyone on your behalf without you clicking approve first.",
  },
  {
    icon: LockKeyhole,
    title: "Credentials encrypted at rest",
    copy: "API keys and tokens are encrypted the moment you save them — never shown again, just the last few characters.",
  },
  {
    icon: History,
    title: "Every action is logged",
    copy: "Every tool call is recorded with its inputs, outputs, latency, and cost before the loop continues.",
  },
] as const;

function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16, filter: "blur(3px)" }}
      whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ type: "spring", stiffness: 300, damping: 32, delay }}
    >
      {children}
    </motion.div>
  );
}

export function LandingPage({ onEnter }: { onEnter: () => void }) {
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  // The glow trails the cursor rather than snapping 1:1 to it — an ambient
  // light source reads as more organic with a little settle (design skill
  // §4/§6: momentum-carrying UI gets a spring, not a hard cut).
  const glowX = useSpring(mx, { stiffness: 120, damping: 22, mass: 0.4 });
  const glowY = useSpring(my, { stiffness: 120, damping: 22, mass: 0.4 });

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    mx.set(event.clientX - rect.left);
    my.set(event.clientY - rect.top);
  }

  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <div className="landing-brand">
            <span className="landing-brand-mark">
              <NetworkMark />
            </span>
            <span className="landing-brand-copy">
              <strong>AI LinkedIn</strong>
              <small>MANAGER</small>
            </span>
          </div>
          <button type="button" className="landing-nav-cta" onClick={onEnter}>
            Sign in <ArrowRight size={15} />
          </button>
        </div>
      </header>

      <section className="landing-hero" onPointerMove={handlePointerMove}>
        <motion.div className="landing-glow" style={{ left: glowX, top: glowY }} aria-hidden="true" />
        <div className="landing-hero-content">
          <motion.span
            className="landing-hero-badge"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 26 }}
          >
            <NetworkMark />
          </motion.span>
          <motion.h1
            initial={{ opacity: 0, y: 14, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ type: "spring", stiffness: 280, damping: 30, delay: 0.05 }}
          >
            Five agents.
            <br />
            One LinkedIn presence, run for you.
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 280, damping: 30, delay: 0.12 }}
          >
            Research finds what's worth saying. A strategist and a writer turn it into a post in your voice.
            An engagement agent handles the replies. Analytics tells you what worked. You approve every
            publish — nothing goes out on its own.
          </motion.p>
          <motion.div
            className="landing-hero-actions"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 280, damping: 30, delay: 0.18 }}
          >
            <button type="button" className="landing-cta-primary" onClick={onEnter}>
              Enter dashboard <ArrowRight size={16} />
            </button>
            <a className="landing-cta-secondary" href="#how-it-works">
              See how it works
            </a>
          </motion.div>
        </div>
      </section>

      <section className="landing-section" id="how-it-works">
        <Reveal>
          <p className="landing-eyebrow">The pipeline</p>
          <h2>Research becomes a draft becomes a decision — yours.</h2>
        </Reveal>
        <div className="landing-pipeline">
          {PIPELINE.map((step, i) => (
            <Reveal key={step.label} delay={i * 0.06}>
              <div className="pipeline-step">
                <span className="pipeline-icon">
                  <step.icon size={18} strokeWidth={1.8} />
                </span>
                <strong>{step.label}</strong>
                <small>{step.detail}</small>
              </div>
            </Reveal>
          ))}
        </div>
        <Reveal delay={0.24}>
          <p className="landing-pipeline-note">
            Engagement and Analytics run continuously alongside the pipeline — replying to comments and DMs,
            and turning finished posts into performance reports — on the schedule you set.
          </p>
        </Reveal>
      </section>

      <section className="landing-section landing-section-muted">
        <Reveal>
          <p className="landing-eyebrow">The agents</p>
          <h2>Five specialists, one harness.</h2>
        </Reveal>
        <div className="landing-agent-grid">
          {AGENTS.map((agent, i) => (
            <Reveal key={agent.name} delay={i * 0.05}>
              <article className="agent-card">
                <span className="agent-card-icon">
                  <agent.icon size={19} strokeWidth={1.8} />
                </span>
                <h3>{agent.name}</h3>
                <p>{agent.copy}</p>
              </article>
            </Reveal>
          ))}
        </div>
      </section>

      <section className="landing-section">
        <Reveal>
          <p className="landing-eyebrow">Built to be trusted</p>
          <h2>Autonomous where it's safe. Gated where it isn't.</h2>
        </Reveal>
        <div className="landing-trust-grid">
          {TRUST.map((item, i) => (
            <Reveal key={item.title} delay={i * 0.06}>
              <div className="trust-card">
                <span className="trust-card-icon">
                  <item.icon size={18} strokeWidth={1.8} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.copy}</p>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      <footer className="landing-footer">
        <Reveal>
          <h2>Your feed, minus the busywork.</h2>
          <button type="button" className="landing-cta-primary" onClick={onEnter}>
            Enter dashboard <ArrowRight size={16} />
          </button>
        </Reveal>
      </footer>
    </div>
  );
}
