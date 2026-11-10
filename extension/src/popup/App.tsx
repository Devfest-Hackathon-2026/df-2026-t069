import React, { useEffect, useState, useRef } from "react";
import type {
  CaptureStatus,
  MessageToBackground,
  MessageToPopup,
  WarningPayload,
} from "../types/messages";
import "./App.css";


const BACKEND_URL = import.meta.env.MODE === "production"
  ? "https://" + "detector-backend-vaggox657a-an.a.run.app"
  : "http://" + "localhost:8080";

const FRONTEND_URL = import.meta.env.MODE === "production"
  ? "https://" + "waiwaiwaiwai.web.app"
  : "http://" + "localhost:3000";

export const App: React.FC = () => {
  const [status, setStatus] = useState<CaptureStatus>("idle");
  const [isMeetTab, setIsMeetTab] = useState<boolean>(false);
  const [isInMeeting, setIsInMeeting] = useState<boolean>(false);
  const [activeTabId, setActiveTabId] = useState<number | undefined>(undefined);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState<number>(0);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);
  const [detectionCount, setDetectionCount] = useState<number>(0);
  const [lastSessionId, setLastSessionId] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  // 1. 初期化：タブ判定、会議室入室チェック、録音ステータス取得、バックエンド死活監視
  useEffect(() => {
    // 現在のタブが Meet かつ会議室に入っているかを判定
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const currentTab = tabs[0];
      if (currentTab?.id && currentTab.url?.startsWith("https://meet.google.com/")) {
        setIsMeetTab(true);
        setActiveTabId(currentTab.id);

        try {
          // パスの前後スラッシュを除去
          const rawPath = new URL(currentTab.url).pathname.replace(/^\/+|\/+$/g, "");
          // Google Meet の会議室URLパターン: xxx-yyyy-zzz または meet/xxx-yyyy-zzz
          const meetRoomPattern = /(?:^|\/)[a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3}$/i;
          const isRoom = meetRoomPattern.test(rawPath);
          setIsInMeeting(isRoom);
        } catch {
          setIsInMeeting(false);
        }
      } else {
        setIsMeetTab(false);
        setIsInMeeting(false);
      }
    });

    // Background Service Worker の状態を取得
    chrome.runtime.sendMessage(
      { type: "GET_STATUS" } as MessageToBackground,
      (res: MessageToPopup) => {
        if (res && res.type === "STATUS_UPDATE") {
          setStatus(res.status);
          if (typeof res.detectionCount === "number") {
            setDetectionCount(res.detectionCount);
          }
          if (res.lastSessionId || res.sessionId) {
            setLastSessionId(res.lastSessionId || res.sessionId || null);
          }
          if (res.errorMessage) setErrorMessage(res.errorMessage);
        }
      }
    );

    // 状態変更の監視
    const listener = (message: MessageToPopup) => {
      if (message.type === "STATUS_UPDATE") {
        setStatus(message.status);
        if (typeof message.detectionCount === "number") {
          setDetectionCount(message.detectionCount);
        }
        if (message.lastSessionId || message.sessionId) {
          setLastSessionId(message.lastSessionId || message.sessionId || null);
        }
        if (message.status === "idle") {
          setElapsedSec(0);
        }
        if (message.errorMessage) setErrorMessage(message.errorMessage);
      }
    };
    chrome.runtime.onMessage.addListener(listener);

    // バックエンド接続テスト
    checkBackendHealth();

    return () => {
      chrome.runtime.onMessage.removeListener(listener);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // 2. 監視中のタイマーカウント
  useEffect(() => {
    if (status === "recording") {
      timerRef.current = window.setInterval(() => {
        setElapsedSec((prev) => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [status]);

  // バックエンドのヘルスチェック
  const checkBackendHealth = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/health`, {
        signal: AbortSignal.timeout(1500),
      });
      setIsBackendConnected(res.ok);
    } catch {
      setIsBackendConnected(false);
    }
  };

  // 監視開始 / 停止の切り替え
  const handleToggle = () => {
    setErrorMessage(null);
    if (status === "recording") {
      chrome.runtime.sendMessage({ type: "STOP_RECORDING" } as MessageToBackground);
    } else {
      setElapsedSec(0);
      setDetectionCount(0);
      chrome.runtime.sendMessage({
        type: "START_RECORDING",
        targetTabId: activeTabId,
      } as MessageToBackground);
    }
  };

  // Google Meet を開く
  const handleOpenMeet = () => {
    chrome.tabs.create({ url: "https://meet.google.com/" });
  };

  // 新規通話を開始（カメラとマイクを自動オフにする有効期限タイムスタンプを付与）
  const handleStartNewMeeting = () => {
    chrome.storage.local.set({ autoMuteUntil: Date.now() + 30000 }, () => {
      chrome.tabs.create({ url: "https://meet.google.com/new" });
    });
  };

  // セットアップ画面を開く
  const handleOpenSetup = () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("src/permission.html") });
  };

  // 通話後レポート画面を開く
  const handleOpenReport = () => {
    const targetSessionId = lastSessionId || "demo-001";
    chrome.tabs.create({
      url: `${FRONTEND_URL}/report/${encodeURIComponent(targetSessionId)}`,
    });
  };

  // デモ用: 警告表示テストボタン
  const handleTestAlert = () => {
    if (!activeTabId) return;
    setDetectionCount((prev) => prev + 1);

    const testPayload: WarningPayload = {
      verdict: "likely_fake",
      riskScore: 0.88,
      summary: "AI合成音声の特徴（不自然な抑揚・呼吸音の欠如）を検知しました。",
      speaker: "相手 (R)",
      suspiciousUtterance:
        "システムの不具合でお薬の配送に別途手数料が必要です。今すぐ指定口座にお振込みください。",
      recommendedActions: [
        "一度通話を切り、事前に登録された代表番号にかけ直してください",
        "通話相手に別の認証方法（院内IDや確認コード）を求めてください",
      ],
      timestamp: Date.now(),
    };

    chrome.tabs.sendMessage(activeTabId, {
      type: "SHOW_ALERT",
      payload: testPayload,
    });
  };

  // 秒数を mm:ss 形式に整形
  const formatTimer = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  return (
    <div className="popup-container">
      {/* ヘッダー */}
      <header className="popup-header">
        <div className="brand-group">
          <img src="/icons/icon48.png" alt="Mimamori" className="logo-img" />
          <div className="brand-text">
            <span className="brand-name">Mimamori</span>
            <span className="brand-sub">AI なりすまし防御</span>
          </div>
        </div>
        <span className={`status-pill ${status}`}>
          <span className="dot" />
          {status === "recording" ? "リアルタイム監視中" : "待機中"}
        </span>
      </header>

      {/* メイン操作エリア */}
      <main className="popup-main">
        {!isMeetTab ? (
          <div className="notice-card warning">
            <div className="notice-icon">⚠️</div>
            <div className="notice-content">
              <h4>Google Meet が開かれていません</h4>
              <p>Google Meet の通話画面を開いてから操作してください。</p>
            </div>
            <button className="open-meet-button" onClick={handleOpenMeet}>
              📞 Google Meet を開く
            </button>
          </div>
        ) : !isInMeeting ? (
          <div className="notice-card info">
            <div className="notice-icon">🏠</div>
            <div className="notice-content">
              <h4>現在、通話に参加していません</h4>
              <p>会議室（通話中）に入ると、AIによるなりすまし監視を開始できます。</p>
            </div>
            <button className="start-meet-button" onClick={handleStartNewMeeting}>
              ➕ 新規会議を開始する
            </button>
          </div>
        ) : (
          <div className="control-card">
            {/* シールドアイコンとパルス演出 */}
            <div className="shield-visual-wrapper">
              <div
                className={`pulse-ring ${status === "recording" ? "active" : ""}`}
              >
                <span className="shield-center-icon">
                  {status === "recording" ? "🛡️" : "🎙️"}
                </span>
              </div>
            </div>

            <div className="status-heading">
              <h3>
                {status === "recording"
                  ? "通話音声をリアルタイム解析中"
                  : "通話の防御準備が完了しています"}
              </h3>
              <p>
                {status === "recording"
                  ? "左右分離ストリームにより、相手の音声のなりすましや不審発言を常時監視しています。"
                  : "「監視を開始する」を押すと、相手の声と自分の声を分離してAI解析をスタートします。"}
              </p>
            </div>

            {/* 監視中のリアルタイムメトリクス */}
            {status === "recording" && (
              <div className="metrics-grid">
                <div className="metric-box">
                  <span className="metric-label">通話時間</span>
                  <span className="metric-value timer">{formatTimer(elapsedSec)}</span>
                </div>
                <div className="metric-box">
                  <span className="metric-label">検知ステータス</span>
                  <span
                    className={`metric-value ${detectionCount > 0 ? "warning" : "safe"}`}
                  >
                    {detectionCount > 0 ? `🚨 ${detectionCount}件 検知` : "🟢 正常（異常なし）"}
                  </span>
                </div>
              </div>
            )}

            {/* 開始/停止トグルボタン */}
            <button
              className={`toggle-action-button ${
                status === "recording" ? "recording" : "idle"
              }`}
              onClick={handleToggle}
            >
              {status === "recording" ? "🛑 監視を停止する" : "🎙️ 監視を開始する"}
            </button>

            {/* 通話停止後、直前の通話レポートがある場合のショートカットボタン */}
            {status !== "recording" && lastSessionId && (
              <button className="open-report-button" onClick={handleOpenReport}>
                📊 直前の通話後レポートを確認する
              </button>
            )}
          </div>
        )}

        {errorMessage && <div className="error-banner">⚠️ {errorMessage}</div>}

        {/* バックエンド接続状態インジケータ */}
        <div className="backend-status-bar">
          <span
            className={`connection-dot ${
              isBackendConnected === true
                ? "online"
                : isBackendConnected === false
                ? "offline"
                : "checking"
            }`}
          />
          <span className="connection-text">
            {isBackendConnected === true
              ? "バックエンドAPI: 接続中 (8080)"
              : isBackendConnected === false
              ? "バックエンドAPI: 未接続（モックモードで動作）"
              : "API接続を確認中..."}
          </span>
        </div>
      </main>

      {/* フッター */}
      <footer className="popup-footer">
        <div className="footer-actions">
          <button
            className="footer-link-button"
            onClick={handleTestAlert}
            disabled={!isMeetTab}
            title="画面上にテスト警告ポップアップを表示します"
          >
            🚨 警告テスト
          </button>
          <span className="divider">|</span>
          <button
            className="footer-link-button"
            onClick={handleOpenReport}
            title="通話後レポート画面を開きます"
          >
            📊 レポート画面
          </button>
          <span className="divider">|</span>
          <button
            className="footer-link-button"
            onClick={handleOpenSetup}
            title="初期セットアップ・マイク設定ページを開きます"
          >
            ℹ️ セットアップ
          </button>
        </div>
      </footer>
    </div>
  );
};
