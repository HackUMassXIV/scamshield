import { useEffect, useState } from "react";
import { cloud, money, post } from "../lib/api";
import { useApp } from "../lib/context";
import type { Hold } from "../lib/types";

export function HoldModal({ hold, payeeName, onClose, onChanged }: {
  hold: Hold; payeeName: string; onClose: () => void; onChanged: () => void;
}) {
  const { s } = useApp();
  const [left, setLeft] = useState(hold.release_in);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const end = Date.now() + hold.release_in * 1000;
    const t = window.setInterval(() => setLeft(Math.max(0, Math.round((end - Date.now()) / 1000))), 500);
    return () => window.clearInterval(t);
  }, [hold.id, hold.release_in]);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    setErr(null);
    try {
      await cloud(s, `/api/holds/${hold.id}/${path}`, post(body ?? {}));
      onChanged();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const mm = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const open = hold.status === "HELD";

  return (
    <div className="fixed inset-0 z-[1000] flex items-end justify-center bg-ink/50 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="hold-title">
      <div className="w-full max-w-md rounded-t-2xl bg-white p-5 sm:rounded-2xl">
        <h2 id="hold-title" className="text-2xl font-bold">
          {open ? `We paused ${money(hold.transfer.amount)} to ${payeeName}` : `Payment ${hold.status.toLowerCase()}`}
        </h2>
        <p className="mt-2 text-ink-soft">Nothing has left your account. Here's why it looks risky:</p>
        <ul className="mt-3 space-y-2">
          {hold.reasons.map((r) => (
            <li key={r} className="flex gap-2"><span aria-hidden="true" className="mt-2 size-2 shrink-0 rounded-full bg-stop" />{r}</li>
          ))}
        </ul>
        {open && (
          <>
            <button disabled={busy} onClick={() => act("cancel")} className="mt-5 w-full rounded-xl bg-teal py-4 text-lg font-bold text-white">
              Cancel this payment
            </button>
            <div className="mt-5 rounded-xl bg-fog p-4">
              {left > 0 ? (
                <p>If you're sure it's not a scam, you can send it in <b className="tabular-nums">{mm}</b>. Use the time to call the person or company back on a number you already know.</p>
              ) : (
                <>
                  <label htmlFor="confirm" className="block">Type <b>RELEASE</b> to send it anyway.</label>
                  <input id="confirm" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off"
                    className="mt-2 w-full rounded-lg border border-line bg-white px-3 py-2" />
                  <button disabled={busy || typed.trim().toUpperCase() !== "RELEASE"} onClick={() => act("release", { confirm: typed })}
                    className="mt-3 w-full rounded-xl border-2 border-stop py-3 font-bold text-stop disabled:opacity-40">
                    Send {money(hold.transfer.amount)} anyway
                  </button>
                </>
              )}
            </div>
          </>
        )}
        {err && <p className="mt-3 text-stop" role="alert">{err}</p>}
        <button onClick={onClose} className="mt-4 w-full py-2 font-bold text-ink-soft">Close</button>
      </div>
    </div>
  );
}
