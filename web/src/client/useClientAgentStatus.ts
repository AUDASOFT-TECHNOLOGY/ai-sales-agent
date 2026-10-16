import { useCallback } from "react";
import type { MessageKey } from "../i18n";
import type { AgentStateKey } from "../mock/types";
import { clientAgent, type AgentState } from "./api";
import { useFetched } from "./useFetched";

export interface ClientAgentStatus {
  /** What the Pulse draws. Only states the server can really report are produced. */
  pulse: AgentStateKey;
  label: MessageKey;
  /** The server's answer, once there is one. */
  agent?: AgentState;
  loading: boolean;
  failed: boolean;
}

/** The agent's real state, from `GET /client/v1/agent`: the client's own switch plus whether the
 *  agent is ready. Nothing is guessed while loading or when the read fails. */
export function useClientAgentStatus(): ClientAgentStatus {
  const load = useCallback(() => clientAgent(), []);
  const { loading, value, failed } = useFetched(load);
  if (value) {
    if (value.active) return { pulse: "working", label: "agent.page.active", agent: value, loading, failed };
    if (value.state === "paused") return { pulse: "paused", label: "agent.page.paused", agent: value, loading, failed };
    if (value.state === "running") return { pulse: "idle", label: "agent.page.waiting", agent: value, loading, failed };
    return { pulse: "not_live", label: "agent.page.notStarted", agent: value, loading, failed };
  }
  return { pulse: "not_live", label: failed ? "agent.status.unavailable" : "agent.status.loading", loading, failed };
}
