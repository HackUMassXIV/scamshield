// Mirrors shared/labels.py PRETTY
export const PRETTY: Record<string, string> = {
  irs_gov: "IRS / government impersonation",
  immigration_uscis: "Immigration / visa threat",
  tech_support: "Tech support scam",
  rental_deposit: "Rental deposit scam",
  fake_job_check: "Fake job / check scam",
  family_emergency: "Family emergency scam",
  bank_impersonation: "Bank impersonation",
  benign: "Normal call",
};
export const pretty = (t: string) => PRETTY[t] ?? t;
