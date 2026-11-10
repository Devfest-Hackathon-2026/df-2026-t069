"use client";

/** メイン画面。[Role1] レイアウト・UXはRole1が自由に作り込む。状態は store から取得するだけ。 */
import { useState } from "react";
import { VerdictBanner } from "@/components/detection/VerdictBanner";
import { useDetectionStore } from "@/stores/detectionStore";

export default function HomePage() {
  const { status, result, error, analyzeFile, recordAndAnalyze, reset } = useDetectionStore();
  const [context, setContext] = useState("");
  const busy = status === "recording" || status === "analyzing";

  return (
    <main>
      <h1>なりすまし検知</h1>
      <p>その声・その映像、本当に本人ですか？</p>

      <label>
        状況（任意）:
        <input
          value={context}
          maxLength={500}
          onChange={(e) => setContext(e.target.value)}
          placeholder="例: 息子を名乗る人から電話"
          style={{ width: "100%", padding: 8 }}
        />
      </label>

      <div style={{ display: "flex", gap: 12, margin: "16px 0", flexWrap: "wrap" }}>
        <button disabled={busy} onClick={() => recordAndAnalyze("audio", 5000, context)}>
          🎙️ 5秒録音して判定
        </button>
        <button disabled={busy} onClick={() => recordAndAnalyze("video", 5000, context)}>
          📹 5秒録画して判定
        </button>
        <label>
          📁 ファイルを判定
          <input
            type="file"
            accept="audio/*,video/*,image/*"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void analyzeFile(f, context);
            }}
          />
        </label>
      </div>

      <VerdictBanner status={status} result={result} error={error} onClose={reset} />
    </main>
  );
}

