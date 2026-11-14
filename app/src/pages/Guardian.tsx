import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CoachCard } from "../components/CoachCard";
import { RiskRing } from "../components/RiskRing";
import { cloud, edge, money, post } from "../lib/api";
import { edgeWs } from "../lib/config";
import { useApp } from "../lib/context";
import { useSocket } from "../lib/socket";
import type { AnyEv, Coach, Hold, Level, RiskEv, TranscriptEv } from "../lib/types";

type Phase = "idle" | "leg" | "requested" | "live" | "summary";
type Summary = { max_level: Level; scam_type: string; scam_label: string; red_flags: string[]; coach_steps: number; call_id: string };

const AREAS = ["Southwest", "Central", "Northeast", "Orchard Hill", "Sylvan", "North Apartments", "Campus Center",
  "Isenberg", "Du Bois Library", "Downtown Amherst", "Puffton Village", "Brandywine"];

export default function Guardian() {
  const { s, cfg } = useApp();
  const [params] = useSearchParams();
  const [phase, setPhase] = useState<Phase>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [risk, setRisk] = useState<RiskEv | null>(null);
  const [coach, setCoach] = useState<Coach | null>(null);
  const [lines, setLines] = useState<TranscriptEv[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const callId = useRef<string | null>(null);

  const reset = () => { setRisk(null); setCoach(null); setLines([]); setSummary(null); };

  const status = useSocket(edgeWs(s), (ev: AnyEv) => {
    const cid = ev.call_id as string | undefined;
    switch (ev.type) {
      case "leg_connected": reset(); callId.current = cid ?? null; setPhase("leg"); setNotice(null); break;
      case "screening_requested": setPhase("requested"); break;
      case "screening_started": if (callId.current !== cid) reset(); callId.current = cid ?? null; setPhase("live"); break;
      case "transcript": setLines((l) => [...l.slice(-40), ev as unknown as TranscriptEv]); setPhase((p) => (p === "summary" ? p : "live")); break;
      case "risk": setRisk(ev as unknown as RiskEv); if ((ev as unknown as RiskEv).coach) setCoach((ev as unknown as RiskEv).coach); break;
      case "coach":
        setCoach(ev as unknown as Coach);
        if (!ev.replay && s.speakCoach && "speechSynthesis" in window) {
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(new SpeechSynthesisUtterance(String(ev.say)));
        }
        break;
      case "leg_timeout": setPhase("idle"); setNotice("Screening timed out because Start wasn't pressed. Merge again to retry."); break;
      case "summary": setSummary(ev as unknown as Summary); setPhase("summary"); break;
      case "call_ended": if (!ev.replay) setPhase((p) => (p === "live" || p === "requested" ? "summary" : p === "leg" ? "idle" : p)); break;
    }
  });

  const begin = async () => {
    setNotice(null);
    try {
      await edge(s, "/api/flag", post({ user_id: s.userId })); // floor = YELLOW for 10 min, tells the cloud
    } catch (e) {
      setNotice((e as Error).message);
    }
    if (cfg?.guardian_number) window.location.href = `tel:${cfg.guardian_number}`;
    else setNotice("The Guardian phone number isn't set on the server (TWILIO_NUMBER).");
  };
  useEffect(() => {
    if (params.get("screen") === "1" && cfg) setNotice("Tap “Screen this call” to add Guardian to your call.");
  }, [params, cfg]);

  const startScreening = async () => {
    setBusy(true);
    try {
      await edge(s, "/api/screen/start", post({ user_id: s.userId }));
      setPhase("requested");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    await edge(s, "/api/screen/stop", post({ user_id: s.userId })).catch(() => {});
    setPhase("summary");
  };
  const suspicious = () => edge(s, "/api/flag", post({ user_id: s.userId })).then(() => setNotice("Marked as suspicious. Payments are extra-protected for 10 minutes.")).catch((e) => setNotice(e.message));

  const level: Level = risk?.level ?? "GREEN";
  const live = phase === "live" || phase === "requested";

  return (
    <main className={`min-h-[calc(100vh-57px)] ${live ? `signal signal-${level}` : ""}`}>
      <div className="mx-auto max-w-md px-4 pb-16 pt-6">
        {status !== "open" && (
          <p className="mb-4 rounded-lg bg-white px-3 py-2 text-ink-soft">
            {status === "connecting" ? "Connecting to your Guardian…" : "Not connected to your Guardian. Check Settings."}
          </p>
        )}
        {notice && <p className="mb-4 rounded-lg bg-white px-3 py-2" role="status">{notice}</p>}

        {phase === "idle" && <Idle onBegin={begin} number={cfg?.guardian_number} />}

        {phase === "leg" && (
          <section className="text-center">
            <h1 className="mt-6 text-3xl font-bold">Guardian is on the line</h1>
            <p className="mx-auto mt-3 max-w-xs text-lg text-ink-soft">Tap <b className="text-ink">Merge</b> on your phone, then come back and start screening.</p>
            <button onClick={startScreening} disabled={busy} className="mt-8 w-full rounded-2xl bg-teal py-5 text-2xl font-bold text-white disabled:opacity-60">
              {busy ? "Starting…" : "Start screening"}
            </button>
            <p className="mt-4 text-ink-soft">Everyone on the call will hear that it's being screened.</p>
          </section>
        )}

        {live && (
          <>
            <RiskRing level={level} score={risk?.score ?? 0} />
            {phase === "requested" && !risk && <p className="mt-2 text-center text-ink-soft">Playing the screening notice…</p>}
            {risk && risk.scam_type !== "benign" && level !== "GREEN" && (
              <p className="mt-2 text-center text-xl font-bold">{risk.scam_label}</p>
            )}
            {coach && level !== "GREEN" && <div className="mt-5"><CoachCard coach={coach} onHungUp={stop} /></div>}
            {risk && risk.red_flags.length > 0 && (
              <section className="mt-5">
                <h2 className="mb-2 font-bold">What they said</h2>
                <ul className="flex flex-wrap gap-2">
                  {risk.red_flags.map((q) => <li key={q} className="rounded-full bg-white px-3 py-1">“{q}”</li>)}
                </ul>
              </section>
            )}
            <details className="mt-5 rounded-xl bg-white/70 p-3">
              <summary className="cursor-pointer font-bold">Transcript ({lines.length})</summary>
              <p className="mt-1 text-sm text-ink-soft">Names and numbers are removed before anything is shown or sent.</p>
              <ol className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                {lines.map((l, i) => <li key={i}>{l.text}</li>)}
              </ol>
            </details>
            <div className="mt-5 flex gap-3">
              <button onClick={suspicious} className="flex-1 rounded-xl border-2 border-ink/20 bg-white py-3 font-bold">This feels wrong</button>
              <button onClick={stop} className="flex-1 rounded-xl border-2 border-ink/20 bg-white py-3 font-bold">Stop screening</button>
            </div>
          </>
        )}

        {phase === "summary" && <SummaryView summary={summary} onDone={() => { reset(); setPhase("idle"); }} />}
      </div>
    </main>
  );
}

const formatPhone = (n: string) => {
  const d = n.replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : n;
};

function Idle({ onBegin, number }: { onBegin: () => void; number?: string }) {
  return (
    <section>
      <h1 className="mt-4 text-[2.4rem] font-bold leading-[1.1]">On a call that feels off?</h1>
      <p className="mt-3 text-lg text-ink-soft">Add Guardian to the call. It listens for scam tactics, tells you what to say, and holds risky payments for an hour.</p>
      <button onClick={onBegin} className="mt-8 w-full rounded-2xl bg-stop py-6 text-2xl font-bold text-white shadow-[0_6px_0_#9C1A2A] active:translate-y-1 active:shadow-[0_2px_0_#9C1A2A]">
        Screen this call
      </button>
      <ol className="mt-8 space-y-3 text-lg">
        <li className="flex gap-3"><b className="w-6 shrink-0 text-teal">1</b><span>Your phone calls Guardian{number ? ` at ${formatPhone(number)}` : ""} and puts the caller on hold.</span></li>
        <li className="flex gap-3"><b className="w-6 shrink-0 text-teal">2</b><span>Tap <b>Merge</b>, then come back here.</span></li>
        <li className="flex gap-3"><b className="w-6 shrink-0 text-teal">3</b><span>Press <b>Start screening</b>. Everyone hears a short notice.</span></li>
      </ol>
      <p className="mt-8 text-ink-soft">Audio is never saved. Names and numbers are removed before anything leaves your laptop.</p>
    </section>
  );
}

function SummaryView({ summary, onDone }: { summary: Summary | null; onDone: () => void }) {
  const { s } = useApp();
  const [holds, setHolds] = useState<Hold[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [area, setArea] = useState("Campus Center");
  const [reported, setReported] = useState<string | null>(null);

  useEffect(() => {
    cloud<Hold[]>(s, `/api/holds/${s.userId}`).then((h) => setHolds(h.filter((x) => x.status === "HELD"))).catch(() => {});
    cloud<{ payees: { id: string; name: string }[] }>(s, `/api/account/${s.userId}`)
      .then((a) => setNames(Object.fromEntries(a.payees.map((p) => [p.id, p.name]))))
      .catch(() => {});
  }, [s]);

  const scam = summary && summary.max_level !== "GREEN" && summary.scam_type !== "benign";
  const report = async () => {
    try {
      await cloud(s, "/api/reports", post({ user_id: s.userId, scam_type: summary!.scam_type, area, channel: "call" }));
      setReported("Reported. Other students in your area will see it on the campus scam map.");
    } catch (e) {
      setReported((e as Error).message);
    }
  };

  return (
    <section>
      <h1 className="mt-4 text-3xl font-bold">{scam ? "That looked like a scam" : "Call ended"}</h1>
      {summary && (
        <p className="mt-2 text-lg text-ink-soft">
          {scam ? `${summary.scam_label}. Highest risk: ${summary.max_level.toLowerCase()}.` : "Nothing suspicious stood out."}
        </p>
      )}
      {summary && summary.red_flags.length > 0 && (
        <ul className="mt-4 flex flex-wrap gap-2">
          {summary.red_flags.map((q) => <li key={q} className="rounded-full bg-white px-3 py-1">“{q}”</li>)}
        </ul>
      )}
      <h2 className="mt-6 font-bold">Held payments</h2>
      {holds.length ? (
        <ul className="mt-2 space-y-2">
          {holds.map((h) => (
            <li key={h.id} className="rounded-xl bg-white p-3">
              <b>{money(h.transfer.amount)}</b> to {names[h.transfer.payee_id] ?? h.transfer.payee_id} is on hold. Review it in Bank.
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-ink-soft">No payments are on hold.</p>
      )}
      {scam && !reported && (
        <div className="mt-6 rounded-xl bg-white p-4">
          <h2 className="font-bold">Warn other students</h2>
          <p className="mt-1 text-ink-soft">Adds the scam type and a rough area to the campus map. Nothing from the call itself.</p>
          <select className="mt-3 w-full rounded-lg border border-line px-3 py-2" value={area} onChange={(e) => setArea(e.target.value)}>
            {AREAS.map((a) => <option key={a}>{a}</option>)}
          </select>
          <button onClick={report} className="mt-3 w-full rounded-xl bg-teal py-3 font-bold text-white">Report to scam map</button>
        </div>
      )}
      {reported && <p className="mt-4" role="status">{reported}</p>}
      <button onClick={onDone} className="mt-6 w-full rounded-xl border-2 border-ink/20 py-3 font-bold">Done</button>
    </section>
  );
}
