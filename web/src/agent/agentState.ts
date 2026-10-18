/** How the agent is drawn by the Pulse. Only states the server can really report are produced. */
export type AgentStateKey =
  | "idle"
  | "working"
  | "writing"
  | "waiting"
  | "observing"
  | "off_hours"
  | "paused"
  | "degraded"
  | "not_live";
