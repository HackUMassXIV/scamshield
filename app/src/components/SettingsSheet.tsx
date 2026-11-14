import { useState } from "react";
import { useApp } from "../lib/context";
import { enablePush } from "../lib/push";

export function SettingsSheet({ onClose }: { onClose: () => void }) {
  const { s, set, cfg, cfgError, reload } = useApp();
  const [pushMsg, setPushMsg] = useState<string | null>(null);
  const field = "w-full rounded-lg border border-line bg-white px-3 py-2 text-base";

  return (
    <div className="fixed inset-0 z-[1000] flex items-end justify-center bg-ink/40 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-t-2xl bg-fog p-5 sm:rounded-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 id="settings-title" className="text-xl font-bold">Settings</h2>
          <button className="rounded-lg px-3 py-1 font-bold text-teal" onClick={onClose}>Done</button>
        </div>

        <label className="mb-3 block">
          <span className="mb-1 block font-bold">You are</span>
          <select className={field} value={s.userId} onChange={(e) => set({ userId: e.target.value })}>
            {(cfg?.users ?? [{ id: s.userId, name: s.userId }]).map((u) => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
        </label>

        <label className="mb-3 block">
          <span className="mb-1 block font-bold">Access key</span>
          <input className={field} type="password" autoComplete="off" value={s.guardianKey}
            onChange={(e) => set({ guardianKey: e.target.value })} placeholder="GUARDIAN_API_KEY from .env" />
        </label>

        <label className="mb-3 flex items-start gap-3">
          <input type="checkbox" className="mt-1 size-5" checked={s.speakCoach} onChange={(e) => set({ speakCoach: e.target.checked })} />
          <span><span className="font-bold">Read coaching aloud</span><br />
            <span className="text-ink-soft">Only with earbuds in, so the caller can't hear it.</span></span>
        </label>

        <button className="mb-1 w-full rounded-lg border border-teal px-3 py-2 font-bold text-teal"
          onClick={async () => setPushMsg(await enablePush(s).catch((e) => e.message))}>
          Turn on notifications
        </button>
        {pushMsg && <p className="mb-3 text-ink-soft">{pushMsg}</p>}

        <details className="mt-4">
          <summary className="cursor-pointer font-bold">Server addresses</summary>
          <p className="my-2 text-ink-soft">Leave blank when this app is opened from the laptop's tunnel link.</p>
          <label className="mb-2 block">
            <span className="mb-1 block">Edge (laptop) URL</span>
            <input className={field} value={s.edgeUrl} onChange={(e) => set({ edgeUrl: e.target.value.trim() })} placeholder={window.location.origin} />
          </label>
          <label className="mb-2 block">
            <span className="mb-1 block">Cloud URL (direct)</span>
            <input className={field} value={s.cloudUrl} onChange={(e) => set({ cloudUrl: e.target.value.trim() })} placeholder="via the edge" />
          </label>
          {s.cloudUrl && (
            <label className="mb-2 block">
              <span className="mb-1 block">Cloud key</span>
              <input className={field} type="password" value={s.cloudKey} onChange={(e) => set({ cloudKey: e.target.value })} />
            </label>
          )}
        </details>

        <p className={`mt-4 ${cfgError ? "text-stop" : "text-ink-soft"}`}>
          {cfgError ? `Not connected: ${cfgError}` : cfg ? `Connected. Speech: ${cfg.asr}, classifier: ${cfg.classifier}, LLM: ${cfg.llm ?? "off"}.` : "Connecting…"}
          {cfgError && <button className="ml-2 font-bold text-teal underline" onClick={reload}>Try again</button>}
        </p>
      </div>
    </div>
  );
}
