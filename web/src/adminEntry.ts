/** The app routes on the URL hash (`/#/admin/login`). A plain `/admin/...` address would land on "/" and show the public
 *  landing page, so it is rewritten to the Admin route before the router starts. `/admin/v1` is the API, never a page. */
export function adminHashFor(pathname: string, search: string, hash: string): string | null {
  const isAdminPath = pathname === "/admin" || pathname.startsWith("/admin/");
  if (!isAdminPath || pathname.startsWith("/admin/v1") || hash.startsWith("#/")) return null;
  return `/#${pathname.replace(/\/+$/, "") || "/admin"}${search}`;
}

export function redirectAdminPath(): void {
  const { pathname, search, hash } = window.location;
  const target = adminHashFor(pathname, search, hash);
  if (target) window.history.replaceState(null, "", target);
}
