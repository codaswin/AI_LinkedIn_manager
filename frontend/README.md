# AI LinkedIn Manager — Dashboard

React + Vite + TypeScript frontend for the [AI LinkedIn Manager](../README.md) backend. Four views, each a thin client over one existing FastAPI resource — no state management library, no component framework, no routing library. The app is small enough that `useState` + four view components cover it.

| View | Backend resource | What it does |
|------|-------------------|----------------|
| Approval Queue | `GET/POST /approvals/*` | Review every pending gated action (publish/schedule/delete/reply/DM/connection-request) with full argument content shown, approve or reject |
| Self-Learning | `GET/POST /learning/proposals/*`, `POST /learning/reflect` | Review reflection-job proposals, trigger an on-demand reflection run |
| Settings | `GET/PUT /settings/{key}` | View/edit agent settings (e.g. `research_agent.poll_interval`) |
| Cost | `GET /cost` | Today's LLM spend vs. the daily cap |

## Run locally

```bash
npm install
cp .env.example .env   # only needed if the backend isn't on http://localhost:8010
npm run dev
```

The backend must be running separately (`uvicorn app.main:app --reload` from `backend/`) and must allow this dev server's origin via `CORS_ALLOWED_ORIGINS` (defaults already cover `http://localhost:5173`).

## Build

```bash
npm run build      # tsc -b && vite build -> dist/
npm run preview    # serve the production build locally
```

## Notes

- **`decided_by` identity**: every approve/reject call needs an actor string, attached server-side to the audit trail. Set via the "Acting as" field in the header — persisted to `localStorage`, defaults to `human:dashboard`.
- **`approveApproval` vs `rejectApproval` response shapes differ** (`src/types.ts`'s `ToolExecutionResult` vs `ApprovalRequest`) — that's a real asymmetry in `backend/app/main.py`, not a frontend bug: approving actually executes the gated tool and returns its raw result; rejecting just updates the approval record.
- No settings-listing endpoint exists (`memory/settings.py` is a key-by-key store) — the Settings view shows the one known seeded key and offers a lookup box for any other key by name, rather than trying to enumerate all settings.
