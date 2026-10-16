/** What to tell the client about a failed prospects / conversations read. */

import type { MessageKey } from "../i18n";
import { ClientApiError } from "./api";

export interface CrmFailure {
  key: MessageKey;
  signedOut?: boolean;
}

export function crmFailureOf(err: unknown): CrmFailure {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return { key: "auth.error.server" };
      case 401:
        return { key: "settings.error.session", signedOut: true };
      case 404:
        return { key: "crm.error.notFound" };
      case 409:
        return { key: "crm.error.notReady" };
      case 422:
        return { key: "crm.error.invalid" };
      case 503:
        return { key: "crm.error.unavailable" };
    }
  }
  return { key: "auth.error.server" };
}
