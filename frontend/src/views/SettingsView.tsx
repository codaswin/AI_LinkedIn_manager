import { useCallback, useEffect, useState } from "react";
import { getSetting, updateSetting } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { SettingValue } from "../types";

// There's no "list all settings" endpoint (memory/settings.py is a
// key-by-key store, not an enumerable one) — this is the one known key
// seeded by DEFAULT_SETTINGS today. The lookup box below covers any other
// key without needing this list kept in sync.
const KNOWN_SETTINGS = ["research_agent.poll_interval"];

function SettingEditor({ settingKey }: { settingKey: string }) {
  const [current, setCurrent] = useState<SettingValue | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSaved(false);
    try {
      const value = await getSetting(settingKey);
      setCurrent(value);
      setDraft(value.value);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCurrent(null);
    } finally {
      setLoading(false);
    }
  }, [settingKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateSetting(settingKey, draft);
      setCurrent(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="card">
      <h3>{settingKey}</h3>
      <ErrorBanner message={error} />
      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          {current?.updated_by && (
            <p className="card-meta">
              Last updated by {current.updated_by}
              {current.updated_at && <> · {new Date(current.updated_at).toLocaleString()}</>}
            </p>
          )}
          <div className="card-actions">
            <input type="text" value={draft} onChange={(e) => setDraft(e.target.value)} />
            <button type="button" onClick={() => void handleSave()} disabled={saving || draft === current?.value}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
          {saved && <p className="info-banner">Saved.</p>}
        </>
      )}
    </article>
  );
}

export function SettingsView() {
  const [lookupKey, setLookupKey] = useState("");
  const [activeKeys, setActiveKeys] = useState<string[]>(KNOWN_SETTINGS);

  function handleLookup() {
    const key = lookupKey.trim();
    if (key && !activeKeys.includes(key)) {
      setActiveKeys((prev) => [...prev, key]);
    }
    setLookupKey("");
  }

  return (
    <section>
      <div className="view-header">
        <h2>Agent Settings</h2>
      </div>
      <p className="empty-state">
        Settings are stored per key (no bulk listing endpoint) — the known key below is always shown; look up any
        other key by name.
      </p>
      <div className="card-actions" style={{ marginBottom: "1.5rem" }}>
        <input
          type="text"
          placeholder="e.g. research_agent.poll_interval"
          value={lookupKey}
          onChange={(e) => setLookupKey(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLookup()}
        />
        <button type="button" onClick={handleLookup}>
          Look up
        </button>
      </div>

      <div className="card-list">
        {activeKeys.map((key) => (
          <SettingEditor key={key} settingKey={key} />
        ))}
      </div>
    </section>
  );
}
