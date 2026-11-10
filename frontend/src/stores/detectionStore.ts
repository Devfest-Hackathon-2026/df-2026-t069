/**
 * 検知フローのグローバル状態。[Role2]
 * UI(Role1) は state を読むだけ・アクションを呼ぶだけにし、副作用はここに集約する。
 */
import { create } from "zustand";
import { detectionApi } from "@/lib/api/detectionClient";
import { MediaCaptureService, type CaptureKind } from "@/features/capture/MediaCaptureService";
import type { DetectionResponse } from "@/types/detection";

export type FlowStatus = "idle" | "recording" | "analyzing" | "done" | "error";

interface DetectionState {
  status: FlowStatus;
  result: DetectionResponse | null;
  error: string | null;
  analyzeFile: (file: File, context?: string) => Promise<void>;
  recordAndAnalyze: (kind: CaptureKind, durationMs?: number, context?: string) => Promise<void>;
  reset: () => void;
}

const capture = new MediaCaptureService();

const extensionOf = (mime: string) => mime.split("/")[1]?.split(";")[0] ?? "bin";

export const useDetectionStore = create<DetectionState>((set) => ({
  status: "idle",
  result: null,
  error: null,

  analyzeFile: async (file, context) => {
    set({ status: "analyzing", error: null, result: null });
    try {
      const result = await detectionApi.detect(file, file.name, context);
      set({ status: "done", result });
    } catch (e) {
      set({ status: "error", error: e instanceof Error ? e.message : "Unknown error" });
    }
  },

  recordAndAnalyze: async (kind, durationMs = 5000, context) => {
    set({ status: "recording", error: null, result: null });
    try {
      await capture.start(kind);
      const blob = await capture.record(durationMs);
      capture.stop();
      set({ status: "analyzing" });
      const result = await detectionApi.detect(blob, `capture.${extensionOf(blob.type)}`, context);
      set({ status: "done", result });
    } catch (e) {
      capture.stop();
      set({ status: "error", error: e instanceof Error ? e.message : "Unknown error" });
    }
  },

  reset: () => set({ status: "idle", result: null, error: null }),
}));

