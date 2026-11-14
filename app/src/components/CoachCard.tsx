import type { Coach } from "../lib/types";

export function CoachCard({ coach, onHungUp }: { coach: Coach; onHungUp: () => void }) {
  const hang = coach.action === "HANG_UP";
  return (
    <section aria-live="assertive" className={`rounded-2xl p-5 ${hang ? "bg-stop text-white" : "bg-white"}`}>
      <p className={`mb-1 ${hang ? "text-white/85" : "text-ink-soft"}`}>Say this</p>
      <p className="text-[2.1rem] font-bold leading-[1.15]">{coach.say}</p>
      <p className={`mt-3 text-lg leading-snug ${hang ? "text-white/90" : "text-ink-soft"}`}>{coach.fact}</p>
      {hang && (
        <button onClick={onHungUp} className="mt-4 w-full rounded-xl bg-white py-4 text-xl font-bold text-stop">
          I've hung up
        </button>
      )}
      {coach.source === "llm" && !hang && <p className="mt-2 text-sm text-ink-soft">Suggested for this call by the on-device AI</p>}
    </section>
  );
}
