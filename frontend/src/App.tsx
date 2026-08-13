import { useState } from "react";
import "./App.css";
import { ActorProvider } from "./ActorProvider";
import { useActor } from "./actorStore";
import { ThemeProvider } from "./ThemeProvider";
import { useTheme } from "./themeStore";
import { ApprovalQueueView } from "./views/ApprovalQueueView";
import { CostView } from "./views/CostView";
import { LearningProposalsView } from "./views/LearningProposalsView";
import { SettingsView } from "./views/SettingsView";

const TABS = [
  { id: "approvals", label: "Approval Queue", render: () => <ApprovalQueueView /> },
  { id: "learning", label: "Self-Learning", render: () => <LearningProposalsView /> },
  { id: "settings", label: "Settings", render: () => <SettingsView /> },
  { id: "cost", label: "Cost", render: () => <CostView /> },
] as const;

function ActorField() {
  const { actor, setActor } = useActor();
  return (
    <label className="actor-field">
      Acting as
      <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} />
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
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("approvals");
  const active = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>AI LinkedIn Manager</h1>
        <div className="header-right">
          <ActorField />
          <ThemeToggle />
        </div>
      </header>
      <nav className="app-nav">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={tab.id === activeTab ? "nav-tab nav-tab-active" : "nav-tab"}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main className="app-main">{active.render()}</main>
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
