import { useCallback, useEffect, useState } from "react";
import { createBrandVoice, deleteBrandVoice, listBrandVoices } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import type { BrandVoice } from "../types";

// Every create/update here dual-writes into the Content Writer/Engagement
// Agents' semantic memory (backend/app/memory/brand_voice.py ingests into
// the RAG "brand_voice" source), so a saved voice is immediately retrievable
// by those agents' run_step() grounding context — not just displayed here.
export function BrandVoiceView() {
  const [voices, setVoices] = useState<BrandVoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setVoices(await listBrandVoices());
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
    if (!title.trim() || !content.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createBrandVoice(title.trim(), content.trim());
      setTitle("");
      setContent("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await deleteBrandVoice(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section>
      <div className="view-header">
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <ErrorBanner message={error} />

      <article className="card" style={{ marginBottom: "1.5rem" }}>
        <h3>New brand voice</h3>
        <div className="card-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <input
            type="text"
            placeholder="Title, e.g. Confident Founder Voice"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            placeholder="Describe the tone, sentence style, vocabulary to use or avoid…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={4}
          />
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={creating || !title.trim() || !content.trim()}
          >
            {creating ? "Saving…" : "Save brand voice"}
          </button>
        </div>
      </article>

      {!loading && voices.length === 0 && (
        <p className="empty-state">No brand voices saved yet — add one above to guide Content Writer and Engagement.</p>
      )}

      <div className="card-list">
        {voices.map((voice) => (
          <article className="card" key={voice.id}>
            <div className="card-title-row">
              <h3>{voice.title}</h3>
              <button
                type="button"
                className="btn-reject"
                disabled={busyId === voice.id}
                onClick={() => void handleDelete(voice.id)}
              >
                Delete
              </button>
            </div>
            <p className="card-meta">Updated {new Date(voice.updated_at).toLocaleString()}</p>
            <p className="card-reason">{voice.content}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
