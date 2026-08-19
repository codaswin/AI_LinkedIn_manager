import { useCallback, useEffect, useState } from "react";
import { deleteCredentials, listCredentials, saveCredentials } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { CredentialType, PlatformCredentialStatus } from "../types";

// Plain-language label for each credential shape — this is the answer to
// "does this platform need a token, a login, an ID+secret pair, or an API
// key" the settings page exists to give, without making the reader learn
// what OAuth means first.
const CREDENTIAL_TYPE_LABEL: Record<CredentialType, string> = {
  api_key: "API key",
  token: "Access token",
  oauth_connected_account: "Login connection",
  client_credentials: "ID + secret",
  endpoint: "Server address",
};

const GROUP_ORDER = ["Publishing to LinkedIn", "AI models", "Research sources"];

function groupPlatforms(platforms: PlatformCredentialStatus[]): [string, PlatformCredentialStatus[]][] {
  const byGroup = new Map<string, PlatformCredentialStatus[]>();
  for (const p of platforms) {
    const list = byGroup.get(p.group) ?? [];
    list.push(p);
    byGroup.set(p.group, list);
  }
  const knownFirst = GROUP_ORDER.filter((g) => byGroup.has(g));
  const rest = [...byGroup.keys()].filter((g) => !GROUP_ORDER.includes(g));
  return [...knownFirst, ...rest].map((g) => [g, byGroup.get(g)!]);
}

function PlatformCard({
  platform,
  onChanged,
  currentUserId,
}: {
  platform: PlatformCredentialStatus;
  onChanged: () => void;
  currentUserId: string;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSave = platform.fields.every((f) => (draft[f.name] ?? "").trim());

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await saveCredentials(platform.id, draft);
      setDraft({});
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    setRemoving(true);
    setError(null);
    try {
      await deleteCredentials(platform.id);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRemoving(false);
    }
  }

  return (
    <form
      className="card connection-card"
      onSubmit={(event) => {
        event.preventDefault();
        void handleSave();
      }}
    >
      <div className="card-title-row">
        <h3>
          {platform.name}
          {platform.required && <span className="badge connection-required-badge">Needed to publish</span>}
        </h3>
        <span className={platform.connected ? "badge badge-approved" : "badge badge-neutral"}>
          {platform.connected ? "Connected" : "Not connected"}
        </span>
      </div>
      <p className="card-meta">
        <span className="mono-chip">{CREDENTIAL_TYPE_LABEL[platform.credential_type]}</span>
        {!platform.required && " · optional"}
      </p>
      <p className="card-reason">{platform.summary}</p>
      {platform.id === "linkedin" && (
        <p className="connection-help">
          Your Composio entity ID is <code className="mono-chip">{currentUserId}</code> — use this when creating
          your LinkedIn connected account in Composio's own dashboard, so it resolves back to your workspace here.
        </p>
      )}
      <ErrorBanner message={error} />

      <div className="connection-fields">
        {platform.fields.map((f) => (
          <label className="connection-field" key={f.name}>
            {f.label}
            <input
              type={f.secret ? "password" : "text"}
              placeholder={
                f.status === "saved_here"
                  ? `Saved — currently ${f.masked_preview}`
                  : f.status === "set_on_server"
                    ? "Already set on the server"
                    : f.placeholder
              }
              value={draft[f.name] ?? ""}
              autoComplete="off"
              onChange={(e) => setDraft((prev) => ({ ...prev, [f.name]: e.target.value }))}
            />
          </label>
        ))}
      </div>

      <p className="connection-help">{platform.help_text}</p>

      <div className="card-actions">
        <button type="submit" disabled={saving || !canSave}>
          {saving ? "Saving…" : "Save"}
        </button>
        {platform.connected && (
          <button type="button" className="btn-reject" onClick={() => void handleRemove()} disabled={removing}>
            {removing ? "Removing…" : "Remove"}
          </button>
        )}
      </div>
    </form>
  );
}

export function ConnectionsView({ currentUserId }: { currentUserId: string }) {
  const [platforms, setPlatforms] = useState<PlatformCredentialStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPlatforms(await listCredentials());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section>
      <div className="view-header">
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <p className="empty-state">
        Paste your logins, keys, and tokens here once — the app remembers them (encrypted) so you don't have to edit
        any config files. Nothing you type is ever shown again after saving; you'll just see the last few characters
        as a reminder of which one is on file.
      </p>
      <ErrorBanner message={error} />

      {groupPlatforms(platforms).map(([group, platformsInGroup]) => (
        <div key={group} className="connection-group">
          <h3 className="connection-group-title">{group}</h3>
          <div className="card-list">
            {platformsInGroup.map((platform) => (
              <PlatformCard
                platform={platform}
                onChanged={() => void refresh()}
                currentUserId={currentUserId}
                key={platform.id}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
