/** The sharing steps of a spreadsheet read through a service account. Connections screen only. */

import { useState } from "react";
import { Copy } from "lucide-react";
import { useI18n } from "../../i18n";
import { Button } from "../../ui/Button";

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      leading={<Copy size={14} aria-hidden />}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setCopied(true));
      }}
    >
      {copied ? t("rv.src.copied") : t("rv.src.copy")}
    </Button>
  );
}

/** A deployment that still uses one service-account file keeps its sharing steps; every other client
 *  connects its own Google account (see `SheetsRow`). */
export function SheetsConnect({ shareWith }: { shareWith: string }) {
  const { t } = useI18n();
  return (
    <ol className="stack" style={{ paddingInlineStart: 20, listStyle: "decimal" }}>
      <li>{t("rv.src.sheets.step1")}</li>
      <li>
        {t("rv.src.sheets.step2")}
        <span className="row row--wrap" style={{ marginBlockStart: 6 }}>
          <code className="t-mono" dir="ltr" data-testid="share-with">
            {shareWith}
          </code>
          <CopyButton text={shareWith} />
        </span>
      </li>
      <li>{t("rv.src.sheets.step3")}</li>
    </ol>
  );
}
