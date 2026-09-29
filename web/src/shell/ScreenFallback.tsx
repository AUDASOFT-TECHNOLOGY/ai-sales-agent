/** Shown inside the shell while a route chunk loads; the rail and bar stay in place (doc 24.3). */
export function ScreenFallback() {
  return (
    <div className="page" aria-busy="true">
      <div className="skeleton" style={{ height: 40, width: 260 }} />
      <div className="skeleton" style={{ height: 260 }} />
    </div>
  );
}
