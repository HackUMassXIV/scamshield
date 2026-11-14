import { useCallback, useEffect, useState } from "react";
import { HoldModal } from "../components/HoldModal";
import { cloud, money, post } from "../lib/api";
import { cloudWs } from "../lib/config";
import { useApp } from "../lib/context";
import { useSocket } from "../lib/socket";
import type { AnyEv, Hold } from "../lib/types";

type Payee = { id: string; name: string; known: boolean };
type Account = { balance: number; payees: Payee[]; recent: { payee_id: string; amount: number; ts: number; memo: string }[] };
type Result = { status: "OK" | "WARN" | "HELD"; reasons: string[]; hold?: Hold; balance?: number; message?: string };

// "Quill Bank" is a made-up demo bank: own name and colors, no real bank's branding.
export default function Bank() {
  const { s } = useApp();
  const [acct, setAcct] = useState<Account | null>(null);
  const [holds, setHolds] = useState<Hold[]>([]);
  const [payee, setPayee] = useState("");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [modal, setModal] = useState<Hold | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [shield, setShield] = useState<{ level: string; session_active: boolean } | null>(null);

  const refresh = useCallback(() => {
    cloud<Account>(s, `/api/account/${s.userId}`).then((a) => { setAcct(a); setErr(null); }).catch((e) => setErr(e.message));
    cloud<Hold[]>(s, `/api/holds/${s.userId}`).then(setHolds).catch(() => {});
  }, [s]);
  useEffect(refresh, [refresh]);

  useSocket(cloudWs(s), (ev: AnyEv) => {
    if (ev.type === "shield") setShield({ level: String(ev.level), session_active: Boolean(ev.session_active) });
    if (["hold_created", "hold_released", "hold_cancelled", "transfer_ok"].includes(ev.type)) refresh();
  });

  const name = (id: string) => acct?.payees.find((p) => p.id === id)?.name ?? id;

  const send = async (confirm = false) => {
    setErr(null);
    setResult(null);
    const amt = Number(amount);
    if (!payee || !(amt > 0)) return setErr("Choose who to pay and enter an amount.");
    try {
      const r = await cloud<Result>(s, `/api/transfer${confirm ? "?confirm=1" : ""}`, post({ user_id: s.userId, payee_id: payee, amount: amt, memo }));
      setResult(r);
      if (r.status === "HELD" && r.hold) setModal(r.hold);
      if (r.status === "OK") { setAmount(""); setMemo(""); }
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const open = holds.filter((h) => h.status === "HELD");
  const field = "w-full rounded-lg border border-[#CFC7DD] bg-white px-3 py-2.5 text-lg";

  return (
    <main className="min-h-[calc(100vh-57px)] bg-[#F3F0F8]">
      <div className="mx-auto max-w-md px-4 pb-16 pt-6">
        <div className="flex items-baseline justify-between">
          <h1 className="text-2xl font-bold text-[#3B2A63]">Quill Bank</h1>
          <span className="text-sm text-ink-soft">Demo bank, not a real bank</span>
        </div>

        {shield?.level === "RED" && (
          <p className="mt-4 rounded-xl bg-stop px-4 py-3 font-bold text-white" role="alert">
            ScamShield flagged a scam call. New or unusual payments will be held for an hour.
          </p>
        )}

        <section className="mt-5 rounded-2xl bg-[#3B2A63] p-5 text-white">
          <p className="text-white/80">Checking</p>
          <p className="mt-1 text-4xl font-bold tabular-nums">{acct ? money(acct.balance) : "…"}</p>
        </section>

        {open.length > 0 && (
          <section className="mt-5">
            <h2 className="mb-2 font-bold">On hold</h2>
            <ul className="space-y-2">
              {open.map((h) => (
                <li key={h.id}>
                  <button onClick={() => setModal(h)} className="flex w-full items-center justify-between rounded-xl border-2 border-stop bg-white px-4 py-3 text-left">
                    <span>{money(h.transfer.amount)} to {name(h.transfer.payee_id)}</span>
                    <span className="font-bold text-stop">Review</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mt-6 rounded-2xl bg-white p-5">
          <h2 className="text-xl font-bold">Send money</h2>
          <label className="mt-4 block">
            <span className="mb-1 block">To</span>
            <select className={field} value={payee} onChange={(e) => setPayee(e.target.value)}>
              <option value="">Choose someone</option>
              {acct?.payees.map((p) => <option key={p.id} value={p.id}>{p.name}{p.known ? "" : " (new)"}</option>)}
            </select>
          </label>
          <label className="mt-3 block">
            <span className="mb-1 block">Amount</span>
            <input className={field} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="0.00" />
          </label>
          <label className="mt-3 block">
            <span className="mb-1 block">Note (optional)</span>
            <input className={field} value={memo} onChange={(e) => setMemo(e.target.value)} maxLength={80} />
          </label>
          <button onClick={() => send()} className="mt-4 w-full rounded-xl bg-[#3B2A63] py-3.5 text-lg font-bold text-white">Send</button>

          {result?.status === "OK" && <p className="mt-3 text-go" role="status">Sent. Your balance is {money(result.balance ?? 0)}.</p>}
          {result?.status === "WARN" && (
            <div className="mt-4 rounded-xl bg-[#FBF1D3] p-4" role="alert">
              <p className="font-bold">Double-check before sending</p>
              <ul className="mt-2 list-disc pl-5">{result.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
              <button onClick={() => send(true)} className="mt-3 w-full rounded-xl border-2 border-ink/30 py-2.5 font-bold">Send anyway</button>
            </div>
          )}
          {err && <p className="mt-3 text-stop" role="alert">{err}</p>}
        </section>

        <section className="mt-6">
          <h2 className="mb-2 font-bold">Recent</h2>
          <ul className="divide-y divide-[#E2DCEC] rounded-2xl bg-white">
            {acct?.recent.map((t, i) => (
              <li key={i} className="flex justify-between px-4 py-3">
                <span>{name(t.payee_id)}<span className="block text-sm text-ink-soft">{t.memo}</span></span>
                <span className="tabular-nums">−{money(t.amount)}</span>
              </li>
            ))}
            {acct && acct.recent.length === 0 && <li className="px-4 py-3 text-ink-soft">No transfers yet.</li>}
          </ul>
        </section>
      </div>
      {modal && <HoldModal hold={modal} payeeName={name(modal.transfer.payee_id)} onClose={() => setModal(null)} onChanged={refresh} />}
    </main>
  );
}
