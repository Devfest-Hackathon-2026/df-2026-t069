"use client";

/** 検知フローの状態と判定結果を表示する警告ポップアップ。 */
import { useEffect, useState } from "react";
import styles from "./VerdictBanner.module.css";
import type { DetectionResponse, Verdict } from "@/types/detection";
import type { FlowStatus } from "@/stores/detectionStore";

const VERDICT_UI: Record<Verdict, { label: string; tone: string }> = {
  authentic: { label: "本物の可能性が高い", tone: "safe" },
  suspicious: { label: "注意が必要です", tone: "caution" },
  likely_fake: { label: "なりすましの可能性が高い", tone: "danger" },
  unknown: { label: "判定できませんでした", tone: "unknown" },
};

interface VerdictBannerProps {
  status: FlowStatus;
  result: DetectionResponse | null;
  error: string | null;
  onClose: () => void;
}

export function VerdictBanner({ status, result, error, onClose }: VerdictBannerProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    setIsExpanded(false);
  }, [status, result]);

  if (status === "idle" || (status === "done" && !result)) return null;

  const isBusy = status === "recording" || status === "analyzing";
  const ui = result ? VERDICT_UI[result.verdict] : null;
  const tone = ui?.tone ?? (status === "error" ? "danger" : "progress");
  const title = status === "recording"
    ? "録音中"
    : status === "analyzing"
      ? "AI解析中"
      : status === "error"
        ? "判定に失敗しました"
        : ui?.label;
  const shouldAssert = status === "error" || (result !== null && result.verdict !== "authentic");

  return (
    <div className={styles.positioner}>
      <section
        className={`${styles.popup} ${styles[tone]}`}
        role={shouldAssert ? "alert" : "status"}
        aria-live={shouldAssert ? "assertive" : "polite"}
        aria-labelledby="detection-popup-title"
      >
        <header className={styles.header}>
          <div className={styles.heading}>
            {isBusy && <span className={styles.spinner} aria-hidden="true" />}
            <h2 id="detection-popup-title">{title}</h2>
          </div>
          {(status === "done" || status === "error") && (
            <button
              type="button"
              className={styles.closeButton}
              onClick={onClose}
              aria-label="閉じて最初に戻る"
              title="閉じて最初に戻る"
            >
              ×
            </button>
          )}
        </header>

        {isBusy && (
          <p className={styles.statusText}>
            {status === "recording" ? "メディアを取得しています。" : "判定結果を確認しています。"}
          </p>
        )}

        {status === "error" && (
          <>
            <p className={styles.summary}>{error || "処理を完了できませんでした。"}</p>
            <p className={styles.disclaimer}>
              時間をおいて再度お試しください。
              <br />
              解決しない場合は、別の方法で確認してください。
            </p>
          </>
        )}

        {status === "done" && result && (
          <>
            <p className={styles.summary}>{result.summary}</p>
                <p className={styles.disclaimer}>
                  AIによる判定は参考情報です。本人確認を保証するものではありません。
                </p>

            <button
              type="button"
              className={styles.expandButton}
              aria-expanded={isExpanded}
              onClick={() => setIsExpanded((expanded) => !expanded)}
            >
              {isExpanded ? "詳細を閉じる" : "判定理由と行動を確認"}
            </button>

            {isExpanded && (
              <div className={styles.details}>
                {result.evidences.length > 0 && (
                  <section className={styles.detailSection}>
                    <h3>判定の根拠</h3>
                    <ul>
                      {result.evidences.map((evidence, index) => (
                        <li key={`${evidence.category}-${index}`}>
                          <strong>{evidence.category}</strong>
                          <span>{evidence.description}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {result.recommended_actions.length > 0 && (
                  <section className={styles.detailSection}>
                    <h3>おすすめの行動</h3>
                    <ol>
                      {result.recommended_actions.map((action, index) => (
                        <li key={`${action}-${index}`}>{action}</li>
                      ))}
                    </ol>
                  </section>
                )}

                <small className={styles.metadata}>
                  判定スコア {(result.risk_score * 100).toFixed(0)}% · {result.processing_ms}ms
                </small>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

