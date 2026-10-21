import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import * as Tooltip from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import { IconButton } from "./Button";
import { useI18n } from "../i18n";

/* ── Modal (L4, doc 21.1). Becomes a bottom sheet on small screens via CSS. ── */
interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  footer?: ReactNode;
  tint?: "success" | "agent" | "attention";
}

export function Modal({ open, onOpenChange, title, description, size = "md", children, footer, tint }: ModalProps) {
  const { t } = useI18n();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content className="modal glass" data-level="4" data-size={size} data-tint={tint}>
          <header className="modal__head">
            <div>
              <Dialog.Title className="t-h2">{title}</Dialog.Title>
              <Dialog.Description className={description ? "c-2 t-body-sm modal__desc" : "sr-only"}>
                {description ?? title}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <IconButton label={t("common.close")}>
                <X size={20} />
              </IconButton>
            </Dialog.Close>
          </header>
          <div className="modal__body">{children}</div>
          {footer ? <footer className="modal__foot">{footer}</footer> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* ── Command palette shell: same L4 surface, no footer ── */
export function PaletteShell({
  open,
  onOpenChange,
  label,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  label: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content className="palette glass" data-level="4">
          <Dialog.Title className="sr-only">{label}</Dialog.Title>
          <Dialog.Description className="sr-only">{label}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* ── Popover (L2) ── */
export function PopoverCard({
  trigger,
  children,
  label,
  side = "bottom",
  open,
  onOpenChange,
}: {
  trigger: ReactNode;
  children: ReactNode;
  label: string;
  side?: "top" | "bottom" | "left" | "right";
  /** Pass both to control the popover (e.g. to close it after a navigation). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger asChild>{trigger}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="popover glass" data-level="2" side={side} sideOffset={8} collisionPadding={12} aria-label={label}>
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/* ── Tooltip (never the only place information lives) ── */
export function Tip({ label, children }: { label: string; children: ReactNode }) {
  if (!label) return <>{children}</>;
  return (
    <Tooltip.Provider delayDuration={400}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className="tooltip glass" data-level="2" sideOffset={8}>
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

/* ── Side drawer (L4, doc 21.2): where secondary detail lives. Bottom sheet on small screens via CSS. ── */
export function Drawer({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content className="drawer glass" data-level="4" aria-describedby={undefined}>
          <header className="drawer__head">
            <Dialog.Title className="t-h2">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <IconButton label={t("common.close")}>
                <X size={22} />
              </IconButton>
            </Dialog.Close>
          </header>
          <div className="drawer__body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
