import { useEffect, useState, type ComponentType, type FormEvent } from "react";
import { Bot, BrainCircuit, ChevronRight, CircleDollarSign, LockKeyhole, LogOut, Menu, MessageSquareText, Moon, Network, Settings2, ShieldCheck, Sparkles, Sun, UserRound, Workflow, X } from "lucide-react";
import "./App.css";
import { ApiError, getCurrentSession, login, logout } from "./api";
import { ActivityBanner } from "./components/ActivityBanner";
import { ThemeProvider } from "./ThemeProvider";
import { useTheme } from "./themeStore";
import type { DashboardSession } from "./types";
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

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const nextTheme = theme === "light" ? "dark" : "light";
  return <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={`Switch to ${nextTheme} theme`} title={`Switch to ${nextTheme} theme`}>{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button>;
}

function LoginScreen({ onAuthenticated }: { onAuthenticated: (session: DashboardSession) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      onAuthenticated(await login(username, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setSubmitting(false);
    }
  }

  return <main className="login-shell">
    <form className="login-panel" onSubmit={(event) => void handleSubmit(event)}>
      <div className="login-brand"><span className="sidebar-brand-mark"><Bot size={22} /></span><span><strong>AI LinkedIn</strong><small>Manager</small></span></div>
      <div className="login-heading"><span><LockKeyhole size={19} /></span><div><h1>Dashboard sign in</h1><p>Use your assigned workspace account.</p></div></div>
      {error && <p className="login-error" role="alert">{error}</p>}
      <label className="login-field"><span>Username</span><input autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label className="login-field"><span>Password</span><input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <button className="login-submit" type="submit" disabled={submitting}>{submitting ? "Signing in..." : "Sign in"}</button>
    </form>
  </main>;
}

function Dashboard({ session, onSignedOut }: { session: DashboardSession; onSignedOut: () => void }) {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("workflows");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const active = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];
  const ActiveIcon = active.icon;

  useEffect(() => {
    if (!sidebarOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setSidebarOpen(false); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [sidebarOpen]);

  const selectTab = (id: (typeof TABS)[number]["id"]) => { setActiveTab(id); setSidebarOpen(false); };
  const handleLogout = async () => { await logout().catch(() => undefined); onSignedOut(); };

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
        <div className="sidebar-footer">
          <div className="sidebar-user"><span className="sidebar-user-avatar"><UserRound size={16} /></span><span className="sidebar-user-details"><strong>{session.user.username}</strong><small>{session.user.role}</small></span></div>
          <button type="button" className="theme-toggle" onClick={() => void handleLogout()} aria-label="Sign out" title="Sign out"><LogOut size={17} /></button>
          <ThemeToggle />
        </div>
      </aside>
      <main className="app-main">
        <div className="page-context"><span className="page-context-icon"><ActiveIcon size={18} /></span><span>{active.group}</span><ChevronRight size={13} /><strong>{active.label}</strong></div>
        <div className="view-transition" key={active.id}>{active.render()}</div>
      </main>
    </div>
  </div>;
}

function AuthenticatedApp() {
  const [session, setSession] = useState<DashboardSession | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    getCurrentSession()
      .then(setSession)
      .catch((error: unknown) => { if (!(error instanceof ApiError) || error.status !== 401) console.error(error); })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <div className="auth-loading"><span className="auth-spinner" /><span>Checking session</span></div>;
  if (!session) return <LoginScreen onAuthenticated={setSession} />;
  return <Dashboard session={session} onSignedOut={() => setSession(null)} />;
}

export default function App() {
  return <ThemeProvider><AuthenticatedApp /></ThemeProvider>;
}
