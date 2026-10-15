/** What to tell the client about a failed lanes call, in the interface language. The server's own
 *  sentences (English) are added only for 409/422, where the contract says they state a rule and
 *  never a value. */

import type { MessageKey } from "../i18n";
import { ClientApiError } from "./api";

export interface LanesFailure {
  key: MessageKey;
  detail?: string;
  /** The server's state moved or refuses this: what is on screen must be read again. */
  reload?: boolean;
  /** The session ended: the caller signs the client out. */
  signedOut?: boolean;
}

export function lanesFailureOf(err: unknown): LanesFailure {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return { key: "auth.error.server" };
      case 401:
        return { key: "lanes.error.session", signedOut: true };
      case 403:
        return { key: "lanes.error.forbidden" };
      case 404:
        return { key: "lanes.error.notFound", reload: true };
      case 409:
        return { key: "lanes.error.conflict", detail: err.message, reload: true };
      case 422:
        return { key: "lanes.error.invalid", detail: err.message };
      case 429:
        return { key: "lanes.error.tooMany" };
      case 502:
        return { key: "lanes.error.upstream" };
      case 503:
        return { key: "lanes.error.unavailable" };
    }
  }
  return { key: "auth.error.server" };
}
