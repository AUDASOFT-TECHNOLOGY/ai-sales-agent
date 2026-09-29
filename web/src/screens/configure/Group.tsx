import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export function Group({ title, hint, icon: Icon, children }: { title: string; hint?: string; icon?: LucideIcon; children: ReactNode }) {
  return (
    <div className="cfg-group">
      <div className="cfg-group__head">
        {Icon ? (
          <span className="cfg-group__icon">
            <Icon size={15} aria-hidden />
          </span>
        ) : null}
        <div>
          <h3 className="t-h3">{title}</h3>
          {hint ? <p className="c-2">{hint}</p> : null}
        </div>
      </div>
      {children}
    </div>
  );
}
