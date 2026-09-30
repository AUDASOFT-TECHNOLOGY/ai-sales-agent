/** Generic "not built yet" page for Admin sections that only have a shell + route so far. */

import { Glass } from "../../ui/Glass";

interface AdminPlaceholderPageProps {
  title: string;
  description: string;
}

export function AdminPlaceholderPage({ title, description }: AdminPlaceholderPageProps) {
  return (
    <Glass level={1} className="admin-placeholder">
      <h2 className="t-h2">{title}</h2>
      <p className="c-2">{description}</p>
      <p className="c-3 t-body-sm admin-placeholder__tag">This section will be implemented in a later step.</p>
    </Glass>
  );
}
