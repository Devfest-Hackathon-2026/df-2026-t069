"use client";

import { useEffect, useState } from "react";
import type { SessionReport, ReportDetection } from "@/types/report";
import { getSessionReport, ReportApiError } from "@/lib/api/reportClient";
import styles from "./ReportView.module.css";

type LoadState = "loading" | "success" | "empty" | "error" | "not-found";

const VERDICT_LABELS: Record<string, string> = {
  low: "軽い注意があります",
  authentic: "本物の可能性が高い",
  suspicious: "注意が必要です",
  likely_fake: "なりすましの可能性が高い",
  unknown: "判定できませんでした",
};

const RISK_LABELS: Record<string, string> = {
  high: "高",
  likely_fake: "高",
  medium: "中",
  suspicious: "中",
  low: "低",
  authentic: "低",
};

const RISK_CLASSES: Record<string, string> = {
  high: "high",
  likely_fake: "high",
  medium: "medium",
  suspicious: "medium",
  low: "low",
  authentic: "low",
};

const MOCK_REPORT: SessionReport = {
  session_id: "mock-session",
  started_at: "2026-11-09T10:00:00+09:00",
  ended_at: "2026-11-09T10:12:34+09:00",
  max_risk_score: 0.85,
  overall_verdict: "likely_fake",
  detections_count: 3,
  expire_at: "2026-11-16T10:00:00+09:00",
  detections: [
    {
      id: "d1",
      timestamp_sec: 95,
      speaker: "相手",
      quote: "最近は保険の見直しをされる方が多いんですよ。",
      category: "不自然な会話誘導",
      risk_score: 0.25,
      risk_level: "low",
      reason: "相手の事情を聞く前に、一般論で話を進めようとしています。",
      advice: "相手の所属や用件を確認してください。",
      created_at: "2026-11-09T10:01:35+09:00",
      expire_at: "2026-11-16T10:00:00+09:00",
    },
    {
      id: "d2",
      timestamp_sec: 312,
      speaker: "相手",
      quote: "今日中にお手続きいただければ割引が適用されます。",
      category: "緊急性の強調",
      risk_score: 0.55,
      risk_level: "medium",
      reason: "期限を区切って判断を急がせる表現が含まれています。",
      advice: "その場で判断せず、別の連絡手段で確認してください。",
      created_at: "2026-11-09T10:05:12+09:00",
      expire_at: "2026-11-16T10:00:00+09:00",
    },
    {
      id: "d3",
      timestamp_sec: 588,
      speaker: "相手",
      quote: "口座の暗証番号を教えていただければ、こちらで処理します。",
      category: "機密情報の要求",
      risk_score: 0.85,
      risk_level: "high",
      reason: "暗証番号などの認証情報を聞き出そうとしています。",
      advice: "情報を伝えず通話を終了し、公式の窓口から確認してください。",
      created_at: "2026-11-09T10:09:48+09:00",
      expire_at: "2026-11-16T10:00:00+09:00",
    },
  ],
};

function formatTime(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatDuration(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safeSeconds / 60)}分${safeSeconds % 60}秒`;
}

function getDuration(report: SessionReport): number | null {
  if (!report.ended_at) return null;

  const start = Date.parse(report.started_at);
  const end = Date.parse(report.ended_at);

  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return null;
  }

  return Math.floor((end - start) / 1000);
}

function getRiskClass(level: string): string {
  return RISK_CLASSES[level] ?? "unknown";
}

function getRiskLabel(level: string): string {
  return RISK_LABELS[level] ?? "不明";
}

function getSessionDate(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "日時不明";
  return date.toLocaleString("ja-JP");
}

function getMockMode(): string | null {
  return new URLSearchParams(window.location.search).get("mock");
}

interface ReportViewProps {
  sessionId: string;
}

export function ReportView({ sessionId }: ReportViewProps) {
  const [state, setState] = useState<LoadState>("loading");
  const [report, setReport] = useState<SessionReport | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [incompleteFromLink, setIncompleteFromLink] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const mode = getMockMode();
    setIncompleteFromLink(new URLSearchParams(window.location.search).get("incomplete") === "1");

    setState("loading");
    setReport(null);

    // 明示的に mock パラメータが指定された場合は従来のモックを使用する。
    if (mode !== null) {
      if (mode === "loading") {
        return () => {
          cancelled = true;
        };
      }

      const timer = window.setTimeout(() => {
        if (cancelled) return;

        if (mode === "error") {
          setState("error");
          return;
        }

        const data: SessionReport = {
          ...MOCK_REPORT,
          session_id: sessionId,
          detections: mode === "empty" ? [] : MOCK_REPORT.detections,
          detections_count: mode === "empty" ? 0 : MOCK_REPORT.detections.length,
          overall_verdict: mode === "empty" ? "authentic" : MOCK_REPORT.overall_verdict,
          max_risk_score: mode === "empty" ? 0 : MOCK_REPORT.max_risk_score,
        };

        setReport(data);
        setState(data.detections.length === 0 ? "empty" : "success");
      }, 350);

      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }

    // 通常アクセスではバックエンドから実データを取得する。
    getSessionReport(sessionId)
      .then((data) => {
        if (cancelled) return;

        setReport(data);
        setState(data.detections.length === 0 ? "empty" : "success");
      })
      .catch((error: unknown) => {
        if (cancelled) return;

        if (error instanceof ReportApiError && error.status === 404) {
          setState("not-found");
          return;
        }

        setState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, reloadKey]);
  const retry = () => setReloadKey((value) => value + 1);

  if (state === "loading") {
    return (
      <main className={styles.page} aria-live="polite">
        <h1 className={styles.title}>通話後レポート</h1>
        <section className={styles.stateCard} role="status">
          <span className={styles.spinner} aria-hidden="true" />
          <p>レポートを読み込んでいます…</p>
        </section>
      </main>
    );
  }

  if (state === "error" || state === "not-found" || !report) {
    const notFound = state === "not-found";

    return (
      <main className={styles.page}>
        <h1 className={styles.title}>通話後レポート</h1>
        <section className={`${styles.stateCard} ${styles.error}`} role="alert">
          <h2>
            {notFound
              ? "レポートが見つかりませんでした"
              : "レポートを取得できませんでした"}
          </h2>
          <p>
            {notFound
              ? "セッションIDを確認してください。通話終了後、レポートが保存されていない可能性もあります。"
              : "通信状況を確認して、もう一度お試しください。"}
          </p>
          <button type="button" onClick={retry}>再取得する</button>
        </section>
      </main>
    );
  }

  const duration = getDuration(report);
  const detections = [...report.detections].sort(
    (a, b) => a.timestamp_sec - b.timestamp_sec,
  );
  const maxTimestamp = Math.max(0, ...detections.map((item) => item.timestamp_sec));
  const timelineDuration = Math.max(duration ?? maxTimestamp, maxTimestamp, 1);
  const incomplete = report.analysis_incomplete || incompleteFromLink;
  const verdictLabel = incomplete && report.detections_count === 0
    ? "判定できませんでした"
    : VERDICT_LABELS[report.overall_verdict] ?? "判定不明";

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>CALL REVIEW</p>
        <h1 className={styles.title}>通話後レポート</h1>
        <p className={styles.subTitle}>{getSessionDate(report.started_at)} の通話</p>
      </header>

      {incomplete && (
        <section className={styles.stateCard} role="alert">
          <h2>一部の音声を分析できませんでした</h2>
          <p>取得できた結果のみ表示しています。検出件数が0件でも、不審な発言がないことを意味しません。</p>
        </section>
      )}

      <section className={styles.summary} aria-label="通話の概要">
        <article className={styles.stat}>
          <span className={styles.statLabel}>全体判定</span>
          <strong className={`${styles.verdict} ${styles[getRiskClass(report.overall_verdict)]}`}>
            {verdictLabel}
          </strong>
        </article>
        <article className={styles.stat}>
          <span className={styles.statLabel}>検出件数</span>
          <strong className={styles.statValue}>{report.detections_count}件</strong>
        </article>
        <article className={styles.stat}>
          <span className={styles.statLabel}>通話時間</span>
          <strong className={styles.statValue}>
            {duration === null ? "不明" : formatDuration(duration)}
          </strong>
        </article>
      </section>

      <section className={`${styles.scoreCard} ${styles[getRiskClass(report.overall_verdict)]}`}>
        <div className={styles.scoreHeader}>
          <span>最大リスクスコア</span>
          <strong>{Math.round(report.max_risk_score * 100)}%</strong>
        </div>
        <div
          className={styles.scoreTrack}
          role="progressbar"
          aria-label="最大リスクスコア"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(report.max_risk_score * 100)}
        >
          <span style={{ width: `${Math.min(100, Math.max(0, report.max_risk_score * 100))}%` }} />
        </div>
        <p className={styles.disclaimer}>
          AIによる判定は参考情報です。本人確認を保証するものではありません。
        </p>
      </section>

      {state === "empty" ? (
        <section className={`${styles.stateCard} ${styles.empty}`}>
          <h2>{incomplete ? "分析できた範囲の検出結果は0件です" : "不審な発言は検出されませんでした"}</h2>
          <p>この結果は本人確認を保証するものではありません。</p>
        </section>
      ) : (
        <>
          <section className={styles.section}>
            <h2 className={styles.sectionTitle}>検出位置</h2>
            <p className={styles.sectionDescription}>
              マーカーを選択すると該当する発言に移動します。
            </p>
            <div className={styles.timeline} aria-label="検出位置タイムライン">
              {detections.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`${styles.marker} ${styles[getRiskClass(item.risk_level)]}`}
                  style={{
                    left: `${Math.min(100, (item.timestamp_sec / timelineDuration) * 100)}%`,
                  }}
                  aria-label={`${formatTime(item.timestamp_sec)}、危険度${getRiskLabel(item.risk_level)}の発言へ移動`}
                  onClick={() => {
                    document.getElementById(`detection-${item.id}`)?.scrollIntoView({
                      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
                        ? "auto"
                        : "smooth",
                      block: "center",
                    });
                  }}
                />
              ))}
            </div>
            <div className={styles.timelineTicks}>
              <span>0:00</span>
              <span>{duration === null ? "終了時刻不明" : formatTime(duration)}</span>
            </div>
          </section>

          <section className={styles.section}>
            <h2 className={styles.sectionTitle}>検出された発言</h2>
            <ol className={styles.detectionList}>
              {detections.map((item: ReportDetection) => {
                const riskClass = getRiskClass(item.risk_level);
                return (
                  <li
                    key={item.id}
                    id={`detection-${item.id}`}
                    className={`${styles.detectionItem} ${styles[riskClass]}`}
                  >
                    <div className={styles.detectionMeta}>
                      <time className={styles.timestamp}>
                        {formatTime(item.timestamp_sec)}
                      </time>
                      <span className={`${styles.riskBadge} ${styles[riskClass]}`}>
                        危険度 {getRiskLabel(item.risk_level)}
                      </span>
                    </div>

                    {(item.speaker || item.category) && (
                      <p className={styles.detectionInfo}>
                        {item.speaker && `発言者：${item.speaker}`}
                        {item.speaker && item.category && " ／ "}
                        {item.category}
                      </p>
                    )}

                    <blockquote className={styles.quote}>{item.quote}</blockquote>

                    <div className={styles.detailBlock}>
                      <span className={styles.detailLabel}>検出理由</span>
                      <p>{item.reason}</p>
                    </div>

                    {item.advice && (
                      <div className={styles.advice}>
                        <span className={styles.detailLabel}>おすすめの行動</span>
                        <p>{item.advice}</p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        </>
      )}

      <footer className={styles.footer}>
        <p>セッションID：{report.session_id}</p>
        <p>レポートの保存期間には制限があります。</p>
      </footer>
    </main>
  );
}
