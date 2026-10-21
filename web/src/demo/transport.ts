/** The one door every Client call goes through. Outside demo mode it is the browser's `fetch`,
 *  unchanged. In demo mode the request is answered in memory by `demoApi` and NEVER reaches the
 *  network: there is no fallback to `fetch` in that branch. */

import { demoFetch } from "./demoApi";
import { isDemo } from "./demoMode";

export function appFetch(input: string, init?: RequestInit): Promise<Response> {
  if (isDemo()) return demoFetch(input, init);
  return fetch(input, init);
}
