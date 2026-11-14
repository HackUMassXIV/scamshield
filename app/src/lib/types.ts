export type Level = "GREEN" | "YELLOW" | "RED";

export type Coach = {
  step: number; scam_type: string; scam_label: string; say: string; fact: string;
  action: "HANG_UP" | null; source: "playbook" | "llm"; do_now?: string | null;
};

export type RiskEv = {
  type: "risk"; call_id: string; level: Level; score: number; scam_type: string; scam_label: string;
  red_flags: string[]; features: Record<string, number | null>; coach: Coach | null;
  latency: { asr_ms?: number; clf_ms?: number; llm_ms?: number | null; voice_ms?: number | null };
  max_level: Level; utterance: number; ts: number;
};

export type TranscriptEv = {
  type: "transcript"; call_id: string; speaker: string; text: string; t_start: number; t_end: number;
  redactions: Record<string, number>; latency: { asr_ms: number; queue_ms: number; redact_ms: number }; ts: number;
};

export type AnyEv = { type: string; ts: number; replay?: boolean; [k: string]: unknown };

export type Hold = {
  id: string; transfer: { user_id: string; payee_id: string; amount: number; memo: string };
  reasons: string[]; points: number; status: "HELD" | "RELEASED" | "CANCELLED" | "EXPIRED";
  created: number; release_after: number; release_in: number;
};

export type AppConfig = {
  users: { id: string; name: string; has_trusted_contact: boolean }[];
  guardian_number: string; needs_key: boolean; cloud: boolean;
  llm: string | null; classifier: string; asr: string; alerts: string | null;
};
