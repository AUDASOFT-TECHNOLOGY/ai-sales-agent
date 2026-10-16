/** What to tell the client about a failed settings or agent call. The server's own sentences are
 *  added only for 409/422, where they state a rule and never a value. */

import type { MessageKey } from "../i18n";
import { ClientApiError, type ReadinessCheck } from "./api";

export interface SettingsFailure {
  key: MessageKey;
  detail?: string;
  /** What the server says is still missing (the agent refused to start). */
  reasons?: ReadinessCheck[];
  /** The server's state moved: what is on screen must be read again. */
  reload?: boolean;
  signedOut?: boolean;
}

export function settingsFailureOf(err: unknown): SettingsFailure {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return { key: "auth.error.server" };
      case 401:
        return { key: "settings.error.session", signedOut: true };
      case 404:
        return { key: "settings.error.notFound" };
      case 409:
        // On `start`, a 409 means "not ready" and names what is missing; on a save it means the
        // settings moved.
        return err.reasons.length > 0
          ? { key: "agent.error.notReady", reasons: err.reasons }
          : { key: "settings.error.conflict", detail: err.message, reload: true };
      case 422:
        return { key: "settings.error.invalid", detail: err.message };
      case 503:
        return { key: "settings.error.unavailable" };
    }
  }
  return { key: "auth.error.server" };
}
