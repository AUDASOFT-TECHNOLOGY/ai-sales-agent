/** Test your agent: the agent writes, for real, to a person the client names (a colleague, a friend,
 *  the client's own second address) before it ever contacts a prospect. That person is a prospect like
 *  any other: same offer, same rules, same daily limit. The client picks a circuit, which decides the
 *  channel, gives the address or number, and follows the conversation here. Nothing technical is asked. */

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, FlaskConical, GraduationCap, MessageSquare, RefreshCw, Route, Send } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button, LinkButton } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import {
  clientAgentTest,
  clientAgentTests,
  clientLanes,
  clientStartAgentTest,
  ClientApiError,
  type AgentTest,
  type AgentTestMessage,
  type AgentTestStart,
  type Lane,
} from "../api";
import { Card, Empty, FailureNote, Loading, Note, PageHead, failureOf } from "../kit";
import { useFetched } from "../useFetched";

const REFRESH_MS = 8000;
const REFUSALS: Record<string, MessageKey> = {
  contact_in_use: "rv.test.err.inUse",
  invalid_contact: "rv.test.err.contact",
  channel_not_ready: "rv.test.err.channel",
  agent_not_loaded: "rv.test.err.notReady",
  dry_run: "rv.test.err.dryRun",
  no_channel: "rv.test.err.circuit",
  unknown_circuit: "rv.test.err.circuit",
};

function channelKey(channel: string | null): MessageKey {
  if (channel === "email") return "rv.test.channel.email";
  if (channel === "telegram") return "rv.test.channel.telegram";
  return "rv.test.channel.phone";
}

export function SimulationPage() {
  const { t } = useI18n();
  const [version, setVersion] = useState(0);
  const lanes = useFetched(useCallback(() => clientLanes(), []));
  const tests = useFetched(useCallback(() => { void version; return clientAgentTests(); }, [version]));
  const [selected, setSelected] = useState<string | null>(null);
  const reload = () => setVersion((v) => v + 1);

  return (
    <div className="rv-page">
      <PageHead title={t("rv.test.title")} text={t("rv.test.intro")} />
      {lanes.loading ? <Loading /> : null}
      {lanes.failed ? <FailureNote failure={failureOf(lanes.error)} /> : null}
      {lanes.value && lanes.value.lanes.length === 0 ? (
        <Card>
          <Empty
            icon={<Route size={22} />}
            title={t("rv.test.noCircuit")}
            text={t("rv.test.noCircuit.text")}
            action={<LinkButton to="/circuits" variant="primary">{t("rv.test.noCircuit.go")}</LinkButton>}
          />
        </Card>
      ) : null}
      {lanes.value && lanes.value.lanes.length > 0 ? (
        <StartCard
          lanes={lanes.value.lanes}
          onStarted={(id) => {
            setSelected(id);
            reload();
          }}
        />
      ) : null}
      {selected ? <ConversationCard id={selected} onChanged={reload} /> : null}
      <History
        tests={tests.value?.tests}
        loading={tests.loading}
        failed={tests.failed ? failureOf(tests.error) : null}
        selected={selected}
        onSelect={setSelected}
      />
      <div className="rv-actions">
        <LinkButton to="/prepare" variant="secondary" leading={<ArrowLeft size={16} className="mirror" aria-hidden />}>
          {t("rv.prep.sim.back")}
        </LinkButton>
        <LinkButton to="/training" variant="secondary" leading={<GraduationCap size={16} aria-hidden />}>
          {t("rv.test.train")}
        </LinkButton>
      </div>
    </div>
  );
}

function StartCard({ lanes, onStarted }: { lanes: Lane[]; onStarted: (id: string) => void }) {
  const { t } = useI18n();
  const [lane, setLane] = useState(lanes[0]!.id);
  const [contact, setContact] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [result, setResult] = useState<AgentTestStart | null>(null);
  const chosen = lanes.find((l) => l.id === lane) ?? lanes[0]!;
  const channel = chosen.channel.channel;
  const blocked = !chosen.channel.authorized ? t("rv.test.err.channel") : contact.trim().length < 3 ? t("rv.test.needContact") : undefined;

  async function launch() {
    setBusy(true);
    setProblem(null);
    setResult(null);
    try {
      const started = await clientStartAgentTest({ contact: contact.trim(), name: name.trim() || undefined, circuit_id: chosen.id });
      setResult(started);
      setContact("");
      setName("");
      onStarted(started.prospect_id);
    } catch (err) {
      const code = err instanceof ClientApiError ? err.code : null;
      setProblem((code && REFUSALS[code]) || failureOf(err).key);
    }
    setBusy(false);
  }

  return (
    <Card data-testid="agent-test-start">
      <h3>{t("rv.test.start.title")}</h3>
      <div className="stack">
        {lanes.length > 1 ? (
          <div className="rv-choices" role="radiogroup" aria-label={t("rv.test.circuit")}>
            {lanes.map((l) => (
              <button key={l.id} type="button" role="radio" aria-checked={l.id === lane} className="rv-choice" data-on={l.id === lane || undefined} onClick={() => setLane(l.id)}>
                <strong>{l.id}</strong>
                <span>{l.channel.label}</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="c-2">{t("rv.test.circuit.one", { circuit: chosen.id, channel: chosen.channel.label })}</p>
        )}
        <TextInput
          label={t(channelKey(channel))}
          helper={t("rv.test.contact.help")}
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          dir="ltr"
          inputMode={channel === "email" ? "email" : "tel"}
          autoComplete="off"
        />
        <TextInput label={t("rv.test.name")} optional={t("rv.common.optional")} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
        <Note tone="info">{t(channel === "telegram" ? "rv.test.note.telegram" : "rv.test.note")}</Note>
        {problem ? <Note tone="danger" role="alert">{t(problem)}</Note> : null}
        {result ? <ResultNote result={result} /> : null}
        <div className="rv-actions">
          <Button variant="primary" leading={<Send size={16} aria-hidden />} loading={busy} disabled={busy || blocked !== undefined} disabledReason={blocked} onClick={launch}>
            {t("rv.test.launch")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function ResultNote({ result }: { result: AgentTestStart }) {
  const { t } = useI18n();
  if (result.state === "sent") return <Note tone="success" title={t("rv.test.sent")}>{t("rv.test.sent.text")}</Note>;
  if (result.state === "waiting") return <Note tone="warn" title={t("rv.test.waiting")}>{t("rv.test.waiting.text")}</Note>;
  return <Note tone="danger" title={t("rv.test.notSent")}>{t("rv.test.notSent.text")}</Note>;
}

function ConversationCard({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { t, d } = useI18n();
  const [tick, setTick] = useState(0);
  const found = useFetched(useCallback(() => { void tick; return clientAgentTest(id); }, [id, tick]));
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);
  const time = (iso: string) => d(iso, { dateStyle: "short", timeStyle: "short" });
  const messages: AgentTestMessage[] = found.value?.messages ?? [];
  return (
    <Card data-testid="agent-test-conversation">
      <div className="rv-actions">
        <h3>{t("rv.test.conv.title")}</h3>
        <Chip tint="attention" glyph={<FlaskConical size={13} aria-hidden />}>{t("rv.test.badge")}</Chip>
        <Button size="sm" variant="secondary" leading={<RefreshCw size={14} aria-hidden />} onClick={() => { setTick((n) => n + 1); onChanged(); }}>
          {t("rv.test.refresh")}
        </Button>
      </div>
      {found.loading && !found.value ? <Loading /> : null}
      {found.failed ? <FailureNote failure={failureOf(found.error)} /> : null}
      {found.value ? (
        <>
          <p className="c-2">{t("rv.test.conv.with", { name: found.value.test.name, contact: found.value.test.contact })}</p>
          {messages.length === 0 ? (
            <p className="c-2">{t("rv.test.conv.none")}</p>
          ) : (
            <ol className="stack" aria-live="polite">
              {messages.map((m, i) => (
                <li key={i} className={m.direction === "outbound" ? "rv-bubble" : "rv-bubble rv-bubble--you"} data-from={m.direction}>
                  <small className="c-3">{m.direction === "outbound" ? t("rv.test.from.agent") : t("rv.test.from.you")} · {time(m.sent_at)}</small>
                  <p dir="auto" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{m.text}</p>
                </li>
              ))}
            </ol>
          )}
          <Note tone="info">{t("rv.test.conv.reply")}</Note>
          <div className="rv-actions">
            <LinkButton to={`/conversations/${encodeURIComponent(id)}`} variant="secondary" leading={<MessageSquare size={16} aria-hidden />}>
              {t("rv.test.conv.open")}
            </LinkButton>
          </div>
        </>
      ) : null}
    </Card>
  );
}

function History({
  tests,
  loading,
  failed,
  selected,
  onSelect,
}: {
  tests: AgentTest[] | undefined;
  loading: boolean;
  failed: ReturnType<typeof failureOf> | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  if (loading && !tests) return <Loading />;
  if (failed) return <FailureNote failure={failed} />;
  if (!tests || tests.length === 0) return null;
  return (
    <Card data-testid="agent-test-history">
      <h3>{t("rv.test.history")}</h3>
      <ul className="rv-check">
        {tests.map((x) => (
          <li key={x.prospect_id}>
            <button type="button" className="rv-check__nav" aria-current={x.prospect_id === selected || undefined} onClick={() => onSelect(x.prospect_id)}>
              <span className="rv-check__text">
                <span className="rv-check__title">{x.name}</span>
                <small className="c-3" dir="auto">{x.contact} · {t("rv.test.history.counts", { sent: x.messages - x.replies, replies: x.replies })}</small>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
