import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ActorContext, DEFAULT_ACTOR, STORAGE_KEY } from "./actorStore";

export function ActorProvider({ children }: { children: ReactNode }) {
  const [actor, setActorState] = useState<string>(() => localStorage.getItem(STORAGE_KEY) ?? DEFAULT_ACTOR);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, actor);
  }, [actor]);

  return <ActorContext.Provider value={{ actor, setActor: setActorState }}>{children}</ActorContext.Provider>;
}
