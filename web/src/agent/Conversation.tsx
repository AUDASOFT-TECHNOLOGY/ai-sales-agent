import { useState } from "react";
import { motion } from "motion/react";
import { Check, FileImage, Pencil, Send, Sparkles, X, HandHelping, Lock } from "lucide-react";
import { useI18n } from "../i18n";
import type { Conversation, Message, MessagePart } from "../mock/types";
import { Bead } from "../ui/Bead";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { Tip } from "../ui/Overlays";
import { CertaintyPanel } from "./Certainty";

function Parts({ parts }: { parts: MessagePart[] }) {
  const { t } = useI18n();
  return (
    <>
      {parts.map((p, i) =>
        p.verbatim ? (
          <Tip key={i} label={t("convo.verbatim")}>
            <span className="verbatim-inline" tabIndex={0}>
              <Lock size={11} aria-hidden />
              {p.text}
            </span>
          </Tip>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

const AUTHOR_LABEL: Record<Message["author"], string> = { prospect: "", agent: "REVENSA agent", human: "", system: "" };

/** Three authors, distinguishable without color alone: position, glyph and a label on the first bubble (doc 18.2). */
export function MessageBubble({ message, prospectInitials, firstOfRun }: { message: Message; prospectInitials: string; firstOfRun: boolean }) {
  if (message.author === "system") {
    return (
      <li className="msg-system">
        <span className="chip" data-tint="neutral" data-outline>
          {message.parts.map((p) => p.text).join("")} · {message.time}
        </span>
      </li>
    );
  }
  const mine = message.author === "agent" || message.author === "human";
  return (
    <motion.li layout="position" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }} className="msg" data-author={message.author} data-mine={mine || undefined}>
      {!mine ? <Bead initials={prospectInitials} size={28} /> : null}
      <div className="msg__col">
        {firstOfRun && mine ? (
          <p className="msg__who t-label c-3">
            {message.author === "agent" ? (
              <>
                <Sparkles size={12} aria-hidden className="c-agent" /> {AUTHOR_LABEL.agent}
              </>
            ) : (
              <>{message.who}</>
            )}
          </p>
        ) : null}
        <div className="msg__bubble" lang={message.lang} dir="auto">
          <Parts parts={message.parts} />
          {message.media ? (
            <span className="msg__media">
              <FileImage size={18} aria-hidden />
              <span>
                <span className="t-body-sm ledger__strong">{message.media.name}</span>
                <span className="c-3 t-body-sm"> · {message.media.size}</span>
              </span>
              {message.media.possibleProof ? (
                <span className="chip" data-tint="attention" data-dashed>
                  Possible proof
                </span>
              ) : null}
            </span>
          ) : null}
        </div>
        <p className="msg__time c-3 t-body-sm">{message.time}</p>
      </div>
      {message.author === "human" ? <Bead initials="SK" size={28} /> : null}
    </motion.li>
  );
}

/** A proposed message waits in the thread as a ghost bubble (doc 18.5). */
export function DraftBubble({ parts, onApprove, onDecline }: { parts: MessagePart[]; onApprove: (text: string) => void; onDecline: () => void }) {
  const { t } = useI18n();
  const original = parts.map((p) => p.text).join("");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(original);
  return (
    <motion.li layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.97 }} className="msg" data-mine data-author="agent">
      <div className="msg__col msg__col--draft">
        <p className="msg__who t-label c-attention">
          <Sparkles size={12} aria-hidden /> {t("convo.draft")}
        </p>
        <div className="draft">
          {editing ? (
            <textarea className="input well" rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label={t("action.edit")} />
          ) : (
            <p>{text}</p>
          )}
          <div className="row row--wrap">
            <Button variant="primary" size="sm" leading={<Check size={16} aria-hidden />} onClick={() => onApprove(text)}>
              {t("action.approve")}
            </Button>
            <Button variant="secondary" size="sm" leading={<Pencil size={15} aria-hidden />} onClick={() => setEditing((e) => !e)}>
              {editing ? t("action.continue") : t("action.edit")}
            </Button>
            <Button variant="ghost" size="sm" leading={<X size={15} aria-hidden />} onClick={onDecline}>
              {t("action.decline")}
            </Button>
          </div>
        </div>
      </div>
    </motion.li>
  );
}

export function HandoffBanner({ reason, onTakeOver }: { reason: string; onTakeOver: () => void }) {
  const { t } = useI18n();
  return (
    <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.3 }}>
      <Glass level={3} tint="attention" className="handoff">
        <HandHelping size={20} aria-hidden className="c-attention" />
        <div className="handoff__text">
          <p className="t-label c-attention">{t("convo.handoff")}</p>
          <p className="voice voice-s">“{reason}”</p>
        </div>
        <Button variant="primary" size="sm" onClick={onTakeOver}>
          {t("action.takeOver")}
        </Button>
      </Glass>
    </motion.div>
  );
}

export function Composer({ name, onSend, onGiveBack }: { name: string; onSend: (text: string) => void; onGiveBack: () => void }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  return (
    <motion.form
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim()) return;
        onSend(text.trim());
        setText("");
      }}
    >
      <p className="c-action t-body-sm">
        <Sparkles size={13} aria-hidden /> {t("convo.agentPaused")}
      </p>
      <div className="row">
        <input className="input well" placeholder={t("convo.composer", { name })} value={text} onChange={(e) => setText(e.target.value)} aria-label={t("convo.composer", { name })} />
        <Button variant="primary" type="submit" leading={<Send size={16} aria-hidden />} disabled={!text.trim()}>
          {t("action.send")}
        </Button>
      </div>
      <button type="button" className="link-btn" onClick={onGiveBack}>
        {t("action.giveBack")}
      </button>
    </motion.form>
  );
}

/** The agent's notes beside the thread (doc 18.3): what it learned and what it decided. */
export function AgentNotes({ conversation }: { conversation: Conversation }) {
  const { t } = useI18n();
  return (
    <div className="notes">
      <h3 className="t-label c-2">{t("convo.notes")}</h3>
      {conversation.notes.learned.length > 0 ? <CertaintyPanel facts={conversation.notes.learned} /> : null}
      {conversation.notes.decided.length > 0 ? (
        <div className="stack">
          <p className="t-label c-3">{t("convo.decided")}</p>
          {conversation.notes.decided.map((d) => (
            <p key={d} className="voice voice-s notes__decided">
              “{d}”
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
