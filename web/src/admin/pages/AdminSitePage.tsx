/** What visitors and clients read, written by the administrator: the Contact, Privacy Policy and Terms
 *  of Service pages (public) and the bank details clients pay plans into (signed-in clients only).
 *  One text per language; saving an empty text removes it, and a page with no text is not linked. */

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "../../ui/Button";
import { Segmented, Textarea, TextInput } from "../../ui/Controls";
import { Glass } from "../../ui/Glass";
import { useAdminAuth } from "../AdminAuthContext";
import { AdminApiError } from "../api";
import { adminSaveSitePage, adminSitePages, type SiteKey, type SiteLocale, type SitePage } from "../siteApi";

const KEYS: Array<{ value: SiteKey; label: string; help: string }> = [
  { value: "contact", label: "Contact", help: "Public. How visitors reach you: address, e-mail, phone, hours." },
  { value: "privacy", label: "Privacy Policy", help: "Public. Linked from the footer of the landing page." },
  { value: "terms", label: "Terms of Service", help: "Public. Linked from the footer of the landing page." },
  { value: "bank", label: "Bank details", help: "Not public. Shown to a client who pays a plan by bank transfer: account holder, bank, IBAN/RIB, and what to write as the reference." },
];
const LOCALES: Array<{ value: SiteLocale; label: string }> = [
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
  { value: "ar", label: "العربية" },
];

/** The form of one page in one language. It is mounted again (`key`) for each, so it starts from what is
 *  saved without any effect to keep it in step. */
function Editor({ pageKey, locale, saved, canEdit, onSaved }: { pageKey: SiteKey; locale: SiteLocale; saved?: SitePage; canEdit: boolean; onSaved: (pages: SitePage[]) => void }) {
  const [title, setTitle] = useState(saved?.title ?? "");
  const [body, setBody] = useState(saved?.body ?? "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      onSaved(await adminSaveSitePage(pageKey, locale, title.trim(), body.trim()));
      setMessage({ tone: "success", text: body.trim() ? "Saved." : "Removed: this page is no longer published in this language." });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof AdminApiError ? err.message : "Could not save." });
    } finally {
      setSaving(false);
    }
  }

  const dir = locale === "ar" ? "rtl" : "ltr";
  return (
    <form className="admin-form" onSubmit={(e) => void onSubmit(e)}>
      <TextInput label="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} disabled={!canEdit || saving} dir={dir} />
      <Textarea
        label="Text"
        rows={14}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        maxLength={20000}
        disabled={!canEdit || saving}
        dir={dir}
        helper="Plain text: line breaks are kept, HTML is not interpreted. Leave it empty and save to remove this language."
      />
      {message ? (
        <p className={`admin-alert admin-alert--${message.tone}`} role={message.tone === "danger" ? "alert" : "status"}>
          {message.text}
        </p>
      ) : null}
      {canEdit ? (
        <Button type="submit" variant="primary" loading={saving}>
          Save
        </Button>
      ) : (
        <p className="c-3 t-body-sm">Only an owner can edit these texts.</p>
      )}
      {saved ? <p className="c-3 t-body-sm">Last saved by {saved.updated_by}.</p> : null}
    </form>
  );
}

export function AdminSitePage() {
  const { admin } = useAdminAuth();
  const canEdit = admin?.role === "owner";
  const [pages, setPages] = useState<SitePage[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [key, setKey] = useState<SiteKey>("contact");
  const [locale, setLocale] = useState<SiteLocale>("en");

  useEffect(() => {
    let current = true;
    adminSitePages().then(
      (found) => current && setPages(found),
      (err: unknown) => current && setLoadError(err instanceof AdminApiError ? err.message : "Could not load the pages."),
    );
    return () => {
      current = false;
    };
  }, []);

  const written = (k: SiteKey) => LOCALES.filter((l) => pages?.some((p) => p.key === k && p.locale === l.value)).map((l) => l.label);
  const current = KEYS.find((k) => k.value === key)!;
  const saved = pages?.find((p) => p.key === key && p.locale === locale);

  return (
    <div className="admin-clients">
      <Segmented<SiteKey> label="Page" layoutId="admin-site-key" value={key} onChange={setKey} options={KEYS.map(({ value, label }) => ({ value, label }))} />
      {loadError ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {loadError}
        </p>
      ) : null}
      <Glass level={1} className="admin-card admin-site">
        <p className="c-2 t-body-sm">{current.help}</p>
        <p className="c-3 t-body-sm">Written in: {written(key).join(", ") || "nothing yet"}</p>
        <Segmented<SiteLocale> label="Language" layoutId="admin-site-locale" value={locale} onChange={setLocale} options={LOCALES} />
        {pages === null && !loadError ? (
          <p className="c-2" role="status">Loading…</p>
        ) : (
          <Editor key={`${key}/${locale}/${saved?.updated_at ?? ""}`} pageKey={key} locale={locale} saved={saved} canEdit={canEdit} onSaved={setPages} />
        )}
      </Glass>
    </div>
  );
}
