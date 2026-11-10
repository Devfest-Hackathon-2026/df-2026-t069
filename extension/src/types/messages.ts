/**
 * Chrome拡張機能内のコンポーネント間メッセージ型定義。
 *
 * 通信フロー:
 *  - Popup <-> Service Worker (状態取得、開始/停止コマンド)
 *  - Service Worker <-> Offscreen Document (音声キャプチャ制御)
 *  - Service Worker -> Content Script (不審発言アラートのオーバーレイ表示)
 */

export type CaptureStatus = "idle" | "recording" | "error";

export type WarningVerdict = "authentic" | "suspicious" | "likely_fake";

export interface WarningPayload {
  verdict: WarningVerdict;
  riskScore: number; // 0.0 - 1.0
  summary: string;
  speaker: string; // "相手"
  suspiciousUtterance: string; // 不審と判定された発言内容
  recommendedActions: string[];
  timestamp: number;
}

export interface AudioChunkPayload {
  chunkIndex: number;
  startSec: number;
  durationSec: number;
  blobUrl: string; // Offscreen内のBlob URL
  mimeType: string;
  timestamp: number;
}

export interface Utterance {
  id: string; // 発言ID (例: utt_1)
  text: string; // 確定した発言テキスト
  startSec: number; // 通話開始からの秒数
  speaker: "相手" | "自分"; // 話者（ステレオチャンネル分離により確定）
  isFinal: boolean; // 確定フラグ
}

// ========== 送信メッセージ型 ==========

export type MessageToBackground =
  | { type: "GET_STATUS" }
  | { type: "START_RECORDING"; targetTabId?: number }
  | { type: "STOP_RECORDING" };

export type MessageToPopup = {
  type: "STATUS_UPDATE";
  status: CaptureStatus;
  activeTabId?: number | null;
  sessionId?: string | null;
  lastSessionId?: string | null;
  detectionCount?: number;
  errorMessage?: string | null;
};

export type MessageToContentScript =
  | {
      type: "SHOW_ALERT";
      payload: WarningPayload;
    }
  | {
      type: "HIDE_ALERT";
    };

export type MessageToOffscreen =
  | { target: "offscreen"; type: "start"; streamId: string }
  | { target: "offscreen"; type: "stop" };

export type MessageFromOffscreen =
  | { target: "service-worker"; type: "audio-chunk"; payload: AudioChunkPayload }
  | { target: "service-worker"; type: "recording-ready"; blobUrl: string }
  | { target: "service-worker"; type: "recording-error"; error: string };

