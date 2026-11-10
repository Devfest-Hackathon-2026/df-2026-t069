/**
 * API契約（backend/app/schemas/detection.py と1対1で対応）。
 * ⚠ 変更時は docs/api-contract.md と Python スキーマも同時に更新し、Role2・Role3 双方のレビューを受けること。
 */

export type Verdict = "authentic" | "suspicious" | "likely_fake" | "unknown";

export interface Evidence {
  category: string;
  description: string;
  severity: number; // 0..1
}

export interface DetectionResponse {
  verdict: Verdict;
  risk_score: number; // 0=本物らしい, 1=偽物らしい
  summary: string;
  evidences: Evidence[];
  recommended_actions: string[];
  detector: string;
  model: string | null;
  processing_ms: number;
}

