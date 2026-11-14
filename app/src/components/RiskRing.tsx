import type { Level } from "../lib/types";

const COLOR: Record<Level, string> = { GREEN: "#1E9E6A", YELLOW: "#E0A100", RED: "#D3263A" };
const WORD: Record<Level, string> = { GREEN: "Looks normal", YELLOW: "Be careful", RED: "Likely a scam" };

/** The signal: an arc that fills with the stabilized score (0-100). */
export function RiskRing({ level, score }: { level: Level; score: number }) {
  const r = 88;
  const circ = 2 * Math.PI * r;
  const frac = Math.max(0.04, Math.min(1, score / 100));
  return (
    <div className={`relative mx-auto size-56 ring-${level}`} role="img" aria-label={`${WORD[level]}, risk ${Math.round(score)} out of 100`}>
      <svg viewBox="0 0 200 200" className="size-full -rotate-90">
        <circle cx="100" cy="100" r={r} fill="none" stroke="#18222E14" strokeWidth="14" />
        <circle className="ring-arc" cx="100" cy="100" r={r} fill="none" stroke={COLOR[level]} strokeWidth="14"
          strokeLinecap="round" strokeDasharray={`${circ * frac} ${circ}`}
          style={{ transition: "stroke-dasharray 500ms ease, stroke 500ms ease" }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="max-w-[8.5rem] text-[1.7rem] font-bold leading-[1.1]" style={{ color: COLOR[level] }}>{WORD[level]}</span>
        <span className="mt-1 text-ink-soft">risk {Math.round(score)}</span>
      </div>
    </div>
  );
}
