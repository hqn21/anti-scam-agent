export type Verdict = "scam" | "likely_scam" | "uncertain" | "likely_legitimate" | "legitimate";

export interface Curated {
  url: string; domain: string; started_at: string;
  verdict: Verdict; is_scam: boolean; scam_type: string | null;
  payment_explicitly_declined: boolean;
  reasoning: string; risk_factors: string[];
  observation: {
    website_summary: string; form_fields_requested: string[]; unexpected_events: string[];
    login_attempted: boolean; login_outcome: string; credit_card_submitted: boolean;
    payment_outcome: string; outgoing_links: string[]; visit_completed: boolean;
  };
  signals: {
    domain_age_days: number | null; domain_days_until_expiration: number | null;
    registrar: string | null; registrant_country: string | null; privacy_protected: boolean | null;
    tls_issuer: string | null; tls_age_days: number | null; tls_is_free_dv: boolean | null;
    dns_has_mx: boolean | null; dns_nameservers: string[];
  };
  telemetry: {
    duration_s: number; cost_usd: number | null; total_tokens: number;
    stages: { name: string; duration_s: number; total_tokens: number; cost_usd: number | null }[];
  };
}

export interface JobStatus {
  id: string;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  curated?: Curated;
}

export interface AnalysisRow {
  id: string; url: string; domain: string; status: string; source: string;
  created_at: string; finished_at: string | null;
  verdict: Verdict | null; is_scam: number | null; scam_type: string | null;
  duration_s: number | null;
}

export interface Stats {
  total: number;
  by_verdict: Record<string, number>;
  scam_count: number; legit_count: number; uncertain_count: number;
  scam_rate: number;
  scam_types: Record<string, number>;
  avg_duration_s: number; total_cost_usd: number;
}
