import { useState } from "react";
import "./App.css";
import { ActorProvider } from "./ActorProvider";
import { useActor } from "./actorStore";
import { ActivityBanner } from "./components/ActivityBanner";
import { ThemeProvider } from "./ThemeProvider";
import { useTheme } from "./themeStore";
import { ApprovalQueueView } from "./views/ApprovalQueueView";
import { BrandVoiceView } from "./views/BrandVoiceView";
import { ConnectionsView } from "./views/ConnectionsView";
import { CostView } from "./views/CostView";
import { LearningProposalsView } from "./views/LearningProposalsView";
import { SettingsView } from "./views/SettingsView";
import { WorkflowsView } from "./views/WorkflowsView";

const TABS = [
  { id: "workflows", label: "Workflows", icon: "⚡", render: () => <WorkflowsView /> },
  { id: "connections", label: "Connections", icon: "🔌", render: () => <ConnectionsView /> },
  { id: "brand-voice", label: "Brand Voice", icon: "🎙️", render: () => <BrandVoiceView /> },
  { id: "approvals", label: "Approval Queue", icon: "✅", render: () => <ApprovalQueueView /> },
  { id: "learning", label: "Self-Learning", icon: "🧠", render: () => <LearningProposalsView /> },
  { id: "settings", label: "Settings", icon: "⚙️", render: () => <SettingsView /> },
  { id: "cost", label: "Cost", icon: "💳", render: () => <CostView /> },
] as const;

function ActorField() {
  const { actor, setActor } = useActor();
  return (
    <label className="actor-field">
      Your name
      <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} />
      <span className="field-hint">Recorded next to anything you approve, reject, or edit.</span>
    </label>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label="Toggle light/dark theme">
      {theme === "light" ? "🌙 Dark" : "☀️ Light"}
    </button>
  );
}

function Dashboard() {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("workflows");
  const active = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];

  return (
    <div className="app-shell">
      <ActivityBanner />
      <div className="app-body">
        <aside className="app-sidebar">
          <div className="sidebar-brand">
            <span className="sidebar-brand-mark">in</span>
            <span className="sidebar-brand-name">AI LinkedIn Manager</span>
          </div>
          <nav className="sidebar-nav">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={tab.id === activeTab ? "sidebar-item sidebar-item-active" : "sidebar-item"}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="sidebar-item-icon" aria-hidden="true">
                  {tab.icon}
                </span>
                {tab.label}
              </button>
            ))}
          </nav>
          <div className="sidebar-footer">
            <ActorField />
            <ThemeToggle />
          </div>
        </aside>
        <main className="app-main">{active.render()}</main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ActorProvider>
        <Dashboard />
      </ActorProvider>
    </ThemeProvider>
  );
}
