import { useCallback, useEffect, useMemo, useState } from "react";
import { ScamMap, TYPE_COLOR, type Report } from "../components/ScamMap";
import { cloud, money } from "../lib/api";
import { cloudWs, edgeWs } from "../lib/config";
import { useApp } from "../lib/context";
import { useSocket } from "../lib/socket";
import { pretty } from "../lib/labels";
import type { AnyEv, Hold } from "../lib/types";

type Item = { src: "edge" | "cloud"; ev: AnyEv };
type Second = { local: number; gemini: number | null; status?: string; reason?: string; local_type?: string };
const SILENCE_MS = 400; // the VAD waits this long after speech ends (SILENCE_MS on the edge)

const median = (xs: number[]) => {
  if (!xs.length) return null;
  const a = [...xs].sort((x, y) => x - y);
  return a[Math.floor(a.length / 2)];
};
const fmtMs = (v: number | null) => (v == null ? "not running" : v < 10 ? `${v.toFixed(1)} ms` : `${Math.round(v)} ms`);
const time = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const LEVEL_TEXT: Record<string, string> = { GREEN: "text-go", YELLOW: "text-[#9A6E00]", RED: "text-stop" };

function describe({ src, ev }: Item): string {
  const e = ev as Record<string, any>;
  switch (ev.type) {
    case "leg_connected": return "Guardian joined the call";
    case "screening_requested": return "Disclosure playing";
    case "screening_started": return `Screening started (${e.mode})`;
    case "transcript": return `“${e.text}”`;
    case "risk": return `${e.level} ${Math.round(e.score)}${e.scam_type !== "benign" ? `, ${e.scam_label}` : ""}`;
    case "coach": return `Coach step ${e.step}: ${e.say}`;
    case "llm": return `Local LLM ${e.score.toFixed(2)} (${pretty(e.scam_type)})${e.dropped_flags ? `, dropped ${e.dropped_flags} invented quote(s)` : ""}`;
    case "user_flag": return "Student pressed “This feels wrong”";
    case "summary": return `Call summary: max ${e.max_level}`;
    case "call_ended": return `Call ended (${e.reason})`;
    case "shield": return src === "cloud" ? `Cloud shield: ${e.level}${e.session_active ? ", call active" : ""}` : "shield";
    case "hold_created": return `HELD ${money(e.hold.transfer.amount)} to ${e.hold.transfer.payee_id}`;
    case "hold_released": return `Released ${money(e.hold.transfer.amount)}`;
    case "hold_cancelled": return `Cancelled ${money(e.hold.transfer.amount)}`;
    case "transfer_ok": return `Sent ${money(e.amount)} to ${e.payee_id}`;
    case "second_opinion": return e.gemini == null ? `Gemini unavailable (${e.status})` : `Second opinion: local ${e.local.toFixed(2)}, Gemini ${e.gemini.toFixed(2)}`;
    case "report": return `New scam map report: ${pretty(e.report.scam_type)} near ${e.report.area}`;
    default: return ev.type;
  }
}

export default function Ops() {
  const { s, cfg } = useApp();
  const [items, setItems] = useState<Item[]>([]);
  const [shield, setShield] = useState<Record<string, any> | null>(null);
  const [second, setSecond] = useState<Second | null>(null);
  const [holds, setHolds] = useState<Hold[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [week, setWeek] = useState<Record<string, number>>({});
  const [mapErr, setMapErr] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});

  const loadHolds = useCallback(() => cloud<Hold[]>(s, `/api/holds/${s.userId}`).then(setHolds).catch(() => {}), [s]);
  useEffect(() => {
    loadHolds();
    cloud<{ payees: { id: string; name: string }[] }>(s, `/api/account/${s.userId}`)
      .then((a) => setNames(Object.fromEntries(a.payees.map((p) => [p.id, p.name]))))
      .catch(() => {});
    cloud<{ reports: Report[]; week_counts: Record<string, number> }>(s, "/api/reports/map")
      .then((m) => { setReports(m.reports); setWeek(m.week_counts); })
      .catch((e) => setMapErr(e.message));
  }, [s, loadHolds]);

  const add = (src: Item["src"]) => (ev: AnyEv) => {
    if (src === "cloud" && ev.type === "shield") setShield(ev);
    if (ev.type === "second_opinion") setSecond(ev as unknown as Second);
    if (ev.type?.startsWith("hold_") || ev.type === "transfer_ok") loadHolds();
    if (ev.type === "report") setReports((r) => [...r, { ...(ev.report as Report), label: pretty((ev.report as Report).scam_type) }]);
    if (ev.replay && ev.type === "shield") return;
    setItems((l) => [{ src, ev }, ...l].slice(0, 200));
  };
  const edgeStatus = useSocket(edgeWs(s), add("edge"));
  const cloudStatus = useSocket(cloudWs(s), add("cloud"));

  const lat = useMemo(() => {
    const tx = items.filter((i) => i.ev.type === "transcript").slice(0, 30).map((i) => i.ev.latency as Record<string, number>);
    const rk = items.filter((i) => i.ev.type === "risk").slice(0, 30).map((i) => i.ev.latency as Record<string, number | null>);
    const llm = items.filter((i) => i.ev.type === "llm").slice(0, 10).map((i) => Number(i.ev.ms));
    const asr = median(tx.map((t) => t.asr_ms));
    const queue = median(tx.map((t) => t.queue_ms)) ?? 0;
    const clf = median(rk.map((r) => Number(r.clf_ms ?? 0)));
    const voice = median(rk.map((r) => r.voice_ms).filter((v): v is number => v != null));
    return { asr, clf, llm: median(llm), voice, e2e: asr == null ? null : SILENCE_MS + queue + asr + (clf ?? 0) };
  }, [items]);

  const onDevice = cfg && cfg.asr !== "remote";
  return (
    <main className="mx-auto max-w-6xl px-4 pb-16 pt-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-2xl font-bold">Ops</h1>
        {onDevice && <span className="rounded-full bg-teal px-3 py-1 font-bold text-white">On-device · offline-capable</span>}
        <span className="text-ink-soft">Edge {edgeStatus}, cloud {cloudStatus}</span>
      </div>
      {cfg && (
        <p className="mt-2 text-ink-soft">
          Speech: {cfg.asr}. Classifier: {cfg.classifier}. Local LLM: {cfg.llm ?? "off"}. Alerts: {cfg.alerts ?? "off"}.
          Only redacted events reach the cloud.
        </p>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_1.3fr]">
        <div className="space-y-6">
          <section className="rounded-2xl bg-white p-5">
            <h2 className="font-bold">Student</h2>
            {shield ? (
              <p className="mt-2 text-lg">
                <b className={LEVEL_TEXT[shield.level] ?? ""}>{shield.level}</b>
                {shield.scam_type !== "benign" && <>, {pretty(shield.scam_type)}</>}
                {shield.session_active ? ", call in progress" : ", no active call"}
                {shield.last_red_at > 0 && <span className="block text-ink-soft">Last RED at {time(shield.last_red_at)}; payments are held for an hour after.</span>}
              </p>
            ) : <p className="mt-2 text-ink-soft">No calls yet.</p>}
          </section>

          <section className="rounded-2xl bg-white p-5">
            <h2 className="font-bold">Latency (median, this laptop)</h2>
            <dl className="mt-3 grid grid-cols-2 gap-y-2">
              <dt>Speech to text</dt><dd className="text-right tabular-nums">{fmtMs(lat.asr)}</dd>
              <dt>Classifier</dt><dd className="text-right tabular-nums">{fmtMs(lat.clf)}</dd>
              <dt>Local LLM</dt><dd className="text-right tabular-nums">{fmtMs(lat.llm)}</dd>
              <dt>Voice-clone check</dt><dd className="text-right tabular-nums">{fmtMs(lat.voice)}</dd>
              <dt className="font-bold">Speech ends to color</dt><dd className="text-right font-bold tabular-nums">{fmtMs(lat.e2e)}</dd>
            </dl>
            <p className="mt-2 text-sm text-ink-soft">End to end includes the {SILENCE_MS} ms pause the system waits for before closing an utterance.</p>
          </section>

          <section className="rounded-2xl bg-white p-5">
            <h2 className="font-bold">Second opinion</h2>
            {second ? (
              <>
                <p className="mt-2 text-2xl font-bold tabular-nums">
                  Local {second.local.toFixed(2)} · Gemini {second.gemini == null ? "n/a" : second.gemini.toFixed(2)}
                </p>
                <p className="mt-1 text-ink-soft">{second.gemini == null ? `Gemini unavailable: ${second.status}` : second.reason}</p>
              </>
            ) : <p className="mt-2 text-ink-soft">Asked only for borderline calls (local score 0.4–0.75, or the local models disagree).</p>}
          </section>

          <section className="rounded-2xl bg-white p-5">
            <h2 className="font-bold">Payments</h2>
            {holds.length ? (
              <ul className="mt-2 space-y-3">
                {holds.slice().reverse().map((h) => (
                  <li key={h.id}>
                    <b>{money(h.transfer.amount)}</b> to {names[h.transfer.payee_id] ?? h.transfer.payee_id}: <b>{h.status.toLowerCase()}</b> ({h.points} points)
                    <span className="block text-sm text-ink-soft">{h.reasons.join("; ")}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-2 text-ink-soft">No held payments.</p>}
          </section>
        </div>

        <div className="space-y-6">
          <section className="rounded-2xl bg-white p-5">
            <div className="mb-3 flex flex-wrap items-baseline gap-x-4">
              <h2 className="mr-auto font-bold">Campus scam map</h2>
              <span className="text-ink-soft">{reports.length} reports, last 14 days</span>
            </div>
            {mapErr ? <p className="text-stop">{mapErr}</p> : <ScamMap reports={reports} />}
            <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(week).map(([label, n]) => (
                <li key={label} className="flex items-center gap-2">
                  <span className="size-3 rounded-full" style={{ background: TYPE_COLOR[reports.find((r) => r.label === label)?.scam_type ?? ""] ?? "#18222E" }} />
                  {label}: {n} this week
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-2xl bg-white p-5">
            <h2 className="font-bold">Event timeline</h2>
            <ol className="mt-3 max-h-[480px] space-y-1.5 overflow-y-auto">
              {items.filter((i) => !i.ev.replay).map((i, k) => (
                <li key={k} className="grid grid-cols-[4.5rem_3.2rem_1fr] gap-2">
                  <span className="tabular-nums text-ink-soft">{time(i.ev.ts)}</span>
                  <span className={i.src === "edge" ? "text-teal" : "text-[#6B4FA3]"}>{i.src}</span>
                  <span className={i.ev.type === "risk" ? `font-bold ${LEVEL_TEXT[String(i.ev.level)] ?? ""}` : ""}>{describe(i)}</span>
                </li>
              ))}
              {items.length === 0 && <li className="text-ink-soft">Waiting for a call. Start one from Guardian, or run <code>python run.py m1</code>.</li>}
            </ol>
          </section>
        </div>
      </div>
    </main>
  );
}
