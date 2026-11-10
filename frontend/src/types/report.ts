import type { Verdict } from "@/types/detection";

export interface ReportDetection {
  id: string;
  timestamp_sec: number;
  speaker: string | null;
  quote: string;
  category: string;
  risk_score: number;
  risk_level: string;
  reason: string;
  advice: string | null;
  created_at: string;
  expire_at: string | null;
}

export interface SessionReport {
  analysis_incomplete?: boolean;
  session_id: string;
  started_at: string;
  ended_at: string | null;
  max_risk_score: number;
  overall_verdict: Verdict | "low";
  detections_count: number;
  expire_at: string;
  detections: ReportDetection[];
}

export type ReportRisk = "high" | "medium" | "low" | "none";
