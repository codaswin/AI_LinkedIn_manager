import { createContext, useContext } from "react";

// Every approve/reject call needs a `decided_by` identity string, attached
// to the resulting audit trail server-side (ApprovalRequestRecord.decided_by
// / LearningProposalRecord.decided_by). One shared, persisted "acting as"
// value beats re-typing it on every single decision.
export const STORAGE_KEY = "ai-linkedin-manager.actor";
export const DEFAULT_ACTOR = "human:dashboard";

export const ActorContext = createContext<{ actor: string; setActor: (value: string) => void }>({
  actor: DEFAULT_ACTOR,
  setActor: () => {},
});

export function useActor() {
  return useContext(ActorContext);
}
