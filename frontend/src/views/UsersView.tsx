import { useCallback, useEffect, useState } from "react";
import { createUser, listUsers } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { DashboardRole, DashboardUser } from "../types";

// Invite-only account creation — there is no public signup route. Every
// invited user gets their own fully isolated workspace (credentials,
// approvals, activity, brand voice, RAG, scheduled automation); "admin"
// role no longer means "can manage the shared app's credentials" (there
// is no more shared credential set) — it means exactly one thing: can
// invite further users. This view is only reachable by admins (see
// App.tsx's TABS filtering), matching the backend's require_role(minimum
// = "admin") gate on GET/POST /admin/users.
const ROLE_OPTIONS: { value: DashboardRole; label: string; description: string }[] = [
  { value: "operator", label: "Operator", description: "Full read/write access to their own workspace" },
  { value: "viewer", label: "Viewer", description: "Read-only access to their own workspace" },
  { value: "admin", label: "Admin", description: "Everything Operator has, plus can invite new users" },
];

export function UsersView() {
  const [users, setUsers] = useState<DashboardUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<DashboardRole>("operator");
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleCreate() {
    if (!username.trim() || !password) return;
    setCreating(true);
    setError(null);
    try {
      await createUser(username.trim(), password, role);
      setUsername("");
      setPassword("");
      setRole("operator");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <section>
      <div className="view-header">
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <p className="empty-state">
        Invite a teammate by creating an account for them here — they'll sign in with the username/password you
        choose, and get their own private workspace with its own connections, approvals, and automation.
      </p>
      <ErrorBanner message={error} />

      <article className="card" style={{ marginBottom: "1.5rem" }}>
        <h3>Invite a user</h3>
        <div className="card-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <input
            type="text"
            placeholder="Username"
            autoComplete="off"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="password"
            placeholder="Password (at least 12 characters)"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <select value={role} onChange={(e) => setRole(e.target.value as DashboardRole)}>
            {ROLE_OPTIONS.map((option) => (
              <option value={option.value} key={option.value}>
                {option.label} — {option.description}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void handleCreate()} disabled={creating || !username.trim() || !password}>
            {creating ? "Creating…" : "Create user"}
          </button>
        </div>
      </article>

      <div className="card-list">
        {users.map((user) => (
          <article className="card" key={user.id}>
            <div className="card-title-row">
              <h3>{user.username}</h3>
              <span className={user.active ? "badge badge-approved" : "badge badge-neutral"}>
                {user.active ? "Active" : "Disabled"}
              </span>
            </div>
            <p className="card-meta">
              <span className="mono-chip">{user.role}</span>
              {" · "}
              {user.last_login_at ? `Last signed in ${new Date(user.last_login_at).toLocaleString()}` : "Never signed in"}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
