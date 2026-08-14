import { useEffect, useState, type ComponentType } from "react";
import { Bot, BrainCircuit, ChevronRight, CircleDollarSign, Menu, MessageSquareText, Moon, Network, Settings2, ShieldCheck, Sparkles, Sun, UserRound, Workflow, X } from "lucide-react";
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

type NavIcon = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;
const TABS = [
  { id: "workflows", label: "Workflows", description: "Run agent tasks", icon: Workflow, group: "Workspace", render: () => <WorkflowsView /> },
  { id: "approvals", label: "Approval Queue", description: "Review gated actions", icon: ShieldCheck, group: "Workspace", render: () => <ApprovalQueueView /> },
  { id: "connections", label: "Connections", description: "Manage integrations", icon: Network, group: "Workspace", render: () => <ConnectionsView /> },
  { id: "brand-voice", label: "Brand Voice", description: "Define writing style", icon: MessageSquareText, group: "Intelligence", render: () => <BrandVoiceView /> },
  { id: "learning", label: "Self-Learning", description: "Review proposals", icon: BrainCircuit, group: "Intelligence", render: () => <LearningProposalsView /> },
  { id: "settings", label: "Agent Settings", description: "Configure behavior", icon: Settings2, group: "System", render: () => <SettingsView /> },
  { id: "cost", label: "Usage & Cost", description: "Track daily spend", icon: CircleDollarSign, group: "System", render: () => <CostView /> },
] as const satisfies readonly { id: string; label: string; description: string; icon: NavIcon; group: string; render: () => React.ReactNode }[];
const NAV_GROUPS = ["Workspace", "Intelligence", "System"] as const;

function ActorField() {
  const { actor, setActor } = useActor();
  return <div className="sidebar-user"><span className="sidebar-user-avatar"><UserRound size={16} /></span><label className="sidebar-user-details"><span>Acting as</span><input aria-label="Your name" type="text" value={actor} onChange={(event) => setActor(event.target.value)} /></label></div>;
}

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const nextTheme = theme === "light" ? "dark" : "light";
  return <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={`Switch to ${nextTheme} theme`} title={`Switch to ${nextTheme} theme`}>{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button>;
}

function Dashboard() {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("workflows");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const active = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];
  const ActiveIcon = active.icon;

  useEffect(() => {
    if (!sidebarOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [sidebarOpen]);

  const selectTab = (id: (typeof TABS)[number]["id"]) => { setActiveTab(id); setSidebarOpen(false); };

  return <div className="app-shell">
    <ActivityBanner />
    <div className="app-body">
      <header className="mobile-header">
        <button className="mobile-menu-toggle" type="button" onClick={() => setSidebarOpen(true)} aria-label="Open navigation" aria-controls="primary-sidebar" aria-expanded={sidebarOpen}><Menu size={19} /><span>Menu</span></button>
        <div className="mobile-brand"><span className="sidebar-brand-mark"><Bot size={18} /></span><span>AI LinkedIn Manager</span></div>
        <ThemeToggle />
      </header>
      <button className={sidebarOpen ? "sidebar-backdrop sidebar-backdrop-visible" : "sidebar-backdrop"} type="button" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />
      <aside id="primary-sidebar" aria-label="Application sidebar" className={sidebarOpen ? "app-sidebar app-sidebar-open" : "app-sidebar"}>
        <div className="sidebar-top">
          <div className="sidebar-brand"><span className="sidebar-brand-mark"><Bot size={19} strokeWidth={2.2} /></span><span className="sidebar-brand-copy"><strong>AI LinkedIn</strong><span>Manager</span></span></div>
          <button className="sidebar-close" type="button" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><X size={17} /><span>Close</span></button>
        </div>
        <div className="workspace-status"><span className="workspace-status-icon"><Sparkles size={15} /></span><span><strong>Agent workspace</strong><small><i /> Systems operational</small></span></div>
        <nav className="sidebar-nav" aria-label="Primary navigation">
          {NAV_GROUPS.map((group) => <div className="sidebar-group" key={group}>
            <span className="sidebar-group-label">{group}</span>
            {TABS.filter((tab) => tab.group === group).map((tab) => {
              const Icon = tab.icon;
              return <button key={tab.id} type="button" className={tab.id === activeTab ? "sidebar-item sidebar-item-active" : "sidebar-item"} onClick={() => selectTab(tab.id)} aria-current={tab.id === activeTab ? "page" : undefined}>
                <span className="sidebar-item-icon"><Icon size={19} strokeWidth={1.9} /></span>
                <span className="sidebar-item-copy"><strong>{tab.label}</strong><small>{tab.description}</small></span>
                <ChevronRight className="sidebar-item-chevron" size={15} />
              </button>;
            })}
          </div>)}
        </nav>
        <div className="sidebar-footer"><ActorField /><ThemeToggle /></div>
      </aside>
      <main className="app-main">
        <div className="page-context"><span className="page-context-icon"><ActiveIcon size={18} /></span><span>{active.group}</span><ChevronRight size={13} /><strong>{active.label}</strong></div>
        <div className="view-transition" key={active.id}>{active.render()}</div>
      </main>
    </div>
  </div>;
}

export default function App() {
  return <ThemeProvider><ActorProvider><Dashboard /></ActorProvider></ThemeProvider>;
}
