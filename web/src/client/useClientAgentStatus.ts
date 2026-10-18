import { useCallback } from "react";
import type { MessageKey } from "../i18n";
import type { AgentStateKey } from "../agent/agentState";
import { clientAgent, type AgentState } from "./api";
import { useFetched } from "./useFetched";

export interface ClientAgentStatus {
  /** What the Pulse draws. Only states the server can really report are produced. */
  pulse: AgentStateKey;
  statusKey: MessageKey;
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
    if (value.active) return { pulse: "working", statusKey: "rv.agent.active", agent: value, loading, failed };
    if (value.state === "paused") return { pulse: "paused", statusKey: "rv.agent.paused", agent: value, loading, failed };
    if (value.state === "running") return { pulse: "idle", statusKey: "rv.agent.waiting", agent: value, loading, failed };
    return { pulse: "not_live", statusKey: "rv.agent.notStarted", agent: value, loading, failed };
  }
  return { pulse: "not_live", statusKey: failed ? "rv.agent.unavailable" : "rv.agent.loading", loading, failed };
}
