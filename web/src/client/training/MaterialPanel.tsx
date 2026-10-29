/** The material behind a product, for the agent to sell with: what it can really read (documents,
 *  pages, testimonials, notes), what is kept but not read, a place to paste a text, and draft lessons
 *  found in the documents. Nothing is presented as known unless text was really extracted. */

import { useCallback, useState } from "react";
import { FileSearch, Plus, Sparkles } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Textarea, TextInput } from "../../ui/Controls";
import { clientAddResourceNote, clientTrainingSuggestions, type LessonSuggestion, type ProductMaterial } from "../api";
import { Card, FailureNote, Note, failureOf, type Failure } from "../kit";
import { ResourcesPanel } from "../offer/ResourcesPanel";
import { blankLesson, type Lesson, type ProductOption } from "./trainingModel";

type NoteKind = "testimonial" | "document" | "other";
const NOTE_KINDS: NoteKind[] = ["testimonial", "document", "other"];

export function MaterialPanel({ products, material, onChanged, lessons, onLessons }: { products: ProductOption[]; material: ProductMaterial[] | null; onChanged: () => void; lessons: Lesson[]; onLessons: (next: Lesson[]) => void }) {
  const { t, n } = useI18n();
  const [picked, setPicked] = useState<string | null>(null);
  const productId = products.some((p) => p.id === picked) ? picked : (products[0]?.id ?? null);
  const found = material?.find((m) => m.product_id === productId);

  if (products.length === 0) {
    return (
      <Card data-testid="training-material">
        <h3>{t("rv.train.material.title")}</h3>
        <p className="c-2">{t("rv.train.material.noproducts")}</p>
        <div>
          <LinkButton to="/offer/products" variant="primary">
            {t("rv.train.material.products")}
          </LinkButton>
        </div>
      </Card>
    );
  }

  return (
    <Card data-testid="training-material">
      <h3>{t("rv.train.material.title")}</h3>
      <p className="c-2">{t("rv.train.material.text")}</p>

      {products.length > 1 ? (
        <label className="rv-select-label">
          <span className="t-label c-2">{t("rv.train.material.pick")}</span>
          <select className="rv-select" value={productId ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {productId ? (
        <>
          {found && found.readable > 0 ? (
            <Note tone="success">{t("rv.train.material.read", { n: found.readable, chars: n(found.readable_chars) })}</Note>
          ) : (
            <Note tone="warn">{t("rv.train.material.none")}</Note>
          )}

          <ResourcesPanel productId={productId} onLoaded={onChanged} />

          {found && found.unread.length > 0 ? (
            <Note title={t("rv.train.material.unread")}>
              {t("rv.train.material.unread.text")}
              <ul>
                {found.unread.map((u) => {
                  const key = `rv.res.detail.${u.reason ?? "other"}` as MessageKey;
                  const reason = t(key);
                  return (
                    <li key={u.id}>
                      {u.title} · {reason === key ? t("rv.res.detail.other") : reason}
                    </li>
                  );
                })}
              </ul>
            </Note>
          ) : null}

          <NoteForm key={productId} productId={productId} onAdded={onChanged} />
          <Suggestions key={`s-${productId}`} productId={productId} lessons={lessons} onLessons={onLessons} readable={found?.readable ?? 0} />

          <div>
            <p className="t-label c-2">{t("rv.train.material.tips")}</p>
            <ul className="stack c-2">
              {(["rv.train.tip.1", "rv.train.tip.2", "rv.train.tip.3", "rv.train.tip.4"] as MessageKey[]).map((k) => (
                <li key={k}>{t(k)}</li>
              ))}
            </ul>
          </div>
        </>
      ) : null}
    </Card>
  );
}

/** A testimonial, an information sheet or a transcript typed or pasted by the client. */
function NoteForm({ productId, onAdded }: { productId: string; onAdded: () => void }) {
  const { t } = useI18n();
  const [kind, setKind] = useState<NoteKind>("testimonial");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function add() {
    setBusy(true);
    setFailure(null);
    setDone(false);
    try {
      await clientAddResourceNote(productId, { title: title.trim(), text: text.trim(), category: kind });
      setTitle("");
      setText("");
      setDone(true);
      onAdded();
    } catch (err) {
      setFailure(failureOf(err));
    }
    setBusy(false);
  }

  return (
    <div className="rv-fieldset stack" data-testid="training-note">
      <h4>{t("rv.train.note.title")}</h4>
      <p className="c-2">{t("rv.train.note.help")}</p>
      <label className="rv-select-label">
        <span className="t-label c-2">{t("rv.train.note.kind")}</span>
        <select className="rv-select" value={kind} onChange={(e) => setKind(e.target.value as NoteKind)}>
          {NOTE_KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`rv.train.note.kind.${k}` as MessageKey)}
            </option>
          ))}
        </select>
      </label>
      <TextInput label={t("rv.train.note.name")} value={title} maxLength={200} dir="auto" onChange={(e) => setTitle(e.target.value)} />
      <Textarea label={t("rv.train.note.text")} rows={4} maxLength={20000} dir="auto" value={text} onChange={(e) => setText(e.target.value)} />
      {failure ? <FailureNote failure={failure} /> : null}
      {done ? <Note tone="success">{t("rv.train.note.added")}</Note> : null}
      <div>
        <Button size="sm" leading={<Plus size={14} aria-hidden />} loading={busy} disabled={busy || title.trim() === "" || text.trim() === ""} onClick={() => void add()}>
          {t("rv.train.note.add")}
        </Button>
      </div>
    </div>
  );
}

/** Frequent questions and testimonials found in the readable documents; the client keeps what suits. */
function Suggestions({ productId, lessons, onLessons, readable }: { productId: string; lessons: Lesson[]; onLessons: (next: Lesson[]) => void; readable: number }) {
  const { t } = useI18n();
  const [found, setFound] = useState<{ suggestions: LessonSuggestion[]; left_out: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());

  const look = useCallback(async () => {
    setBusy(true);
    setFailure(null);
    try {
      setFound(await clientTrainingSuggestions(productId));
    } catch (err) {
      setFailure(failureOf(err));
    }
    setBusy(false);
  }, [productId]);

  const keyOf = (s: LessonSuggestion) => `${s.kind}|${s.when ?? ""}|${s.text}`;
  function add(s: LessonSuggestion) {
    const lesson = s.kind === "faq" ? blankLesson("faq", { when: s.when ?? "", text: s.text, product: productId }) : blankLesson("proof", { text: s.text, source: s.attribution ?? "", product: productId });
    onLessons([...lessons, lesson]);
    setAdded(new Set(added).add(keyOf(s)));
  }

  return (
    <div className="rv-fieldset stack" data-testid="training-suggestions">
      <h4>{t("rv.train.sug.title")}</h4>
      <p className="c-2">{t("rv.train.sug.text")}</p>
      <div>
        <Button size="sm" leading={<FileSearch size={14} aria-hidden />} loading={busy} disabled={busy || readable === 0} disabledReason={readable === 0 ? t("rv.train.material.none") : undefined} onClick={() => void look()}>
          {t("rv.train.sug.go")}
        </Button>
      </div>
      {failure ? <FailureNote failure={failure} /> : null}
      {found && found.suggestions.length === 0 ? <Note>{t("rv.train.sug.none")}</Note> : null}
      {found && found.suggestions.length > 0 ? (
        <ul className="stack">
          {found.suggestions.map((s) => {
            const done = added.has(keyOf(s));
            return (
              <li key={keyOf(s)} className="rv-fieldset stack">
                <span className="t-label c-2">{t(s.kind === "faq" ? "rv.train.sug.faq" : "rv.train.sug.proof")}</span>
                {s.when ? <strong dir="auto">{s.when}</strong> : null}
                <p dir="auto" style={{ margin: 0 }}>
                  {s.text}
                  {s.attribution ? ` — ${s.attribution}` : ""}
                </p>
                <small className="c-3">{t("rv.train.sug.from", { title: s.source })}</small>
                <div>
                  <Button size="sm" variant="secondary" leading={<Sparkles size={14} aria-hidden />} disabled={done} onClick={() => add(s)}>
                    {done ? t("rv.train.sug.added") : t("rv.train.sug.add")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
      {found && found.left_out > 0 ? <p className="c-3 t-body-sm">{t("rv.train.sug.left", { n: found.left_out })}</p> : null}
      {added.size > 0 ? <p className="c-3 t-body-sm">{t("rv.train.sug.review")}</p> : null}
    </div>
  );
}
