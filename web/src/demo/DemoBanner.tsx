import { RotateCcw } from "lucide-react";
import { useI18n } from "../i18n";
import { Button } from "../ui/Button";

const TEXT = {
  fr: { badge: "Mode Démo", short: "données fictives", text: "Données fictives, tout se passe dans votre navigateur : rien n'est envoyé ni enregistré.", reset: "Réinitialiser la démo" },
  en: { badge: "Demo mode", short: "fictitious data", text: "Fictitious data, everything stays in your browser: nothing is sent or saved.", reset: "Reset the demo" },
  ar: { badge: "وضع العرض", short: "بيانات وهمية", text: "بيانات وهمية، كل شيء يبقى في متصفحك: لا يُرسل ولا يُحفظ أي شيء.", reset: "إعادة تعيين العرض" },
} as const;

/** A thin strip above the Client space while the Demo runs, with the manual reset. */
export function DemoBanner({ onReset }: { onReset: () => void }) {
  const { locale } = useI18n();
  const tx = TEXT[locale as keyof typeof TEXT] ?? TEXT.fr;
  return (
    <>
    {/* On a phone the bottom tab bar sits at the edge: the strip goes above it. */}
    <style>{".demo-banner{bottom:16px}@media (max-width:900px){.demo-banner{bottom:84px}}"}</style>
    <div
      role="status"
      className="demo-banner"
      data-testid="demo-banner"
      title={tx.text}
      style={{
        position: "fixed",
        insetInlineEnd: 16,
        zIndex: 60,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "6px 8px 6px 14px",
        borderRadius: 999,
        background: "var(--glass-2-fill, rgba(30,24,60,.9))",
        border: "1px solid var(--brand-soft)",
        backdropFilter: "blur(12px)",
        fontSize: 13,
      }}
    >
      <span>
        <strong>{tx.badge}</strong> · {tx.short}
      </span>
      <Button size="sm" leading={<RotateCcw size={14} aria-hidden />} onClick={onReset}>
        {tx.reset}
      </Button>
    </div>
    </>
  );
}
