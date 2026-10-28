/** What a provider's page leaves in the address when it sends the client back (`?google=...`, `?hubspot=...`). */

import { outcomeOf } from "./connectionKit";

const GOOGLE = ["connected", "denied", "scope", "invalid", "in_use", "unavailable", "error"] as const;
const OTHER = ["connected", "denied", "scope", "invalid", "unavailable", "error"] as const;

export const googleReturnOf = (search: string) => outcomeOf(search, "google", GOOGLE);
export const sheetsReturnOf = (search: string) => outcomeOf(search, "google_sheets", OTHER);
export const hubspotReturnOf = (search: string) => outcomeOf(search, "hubspot", OTHER);
