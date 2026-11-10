/**
 * Google Meet 画面に注入される警告オーバーレイ管理スクリプト [Issue #7, #15]
 *
 * 設計方針:
 *  - ReactランタイムをMeetページに持ち込まず、ピュアなTypeScript DOM操作で完結。
 *  - 外部モジュールへのimport文をゼロにし、Chromeの「Cannot use import statement outside a module」を根絶。
 *  - textContent による安全な描画（XSS防止・プライバシー保護）。
 */

import type { MessageToContentScript, WarningPayload } from "../types/messages";

const CONTAINER_ID = "waiwai-meet-guard-overlay-root";

export class OverlayManager {
  #container: HTMLElement | null = null;
  #currentBanner: HTMLElement | null = null;

  /** Meet画面上のマウントポイントを初期化 */
  public init(): HTMLElement {
    let container = document.getElementById(CONTAINER_ID);
    if (!container) {
      container = document.createElement("div");
      container.id = CONTAINER_ID;
      document.body.appendChild(container);
    }
    this.#container = container;
    return container;
  }

  /** 不審発言検知時に警告バナーをポップアップ表示 */
  public showAlert(payload: WarningPayload): void {
    const container = this.#container || this.init();

    // 既存の警告があれば差し替える
    this.hideAlert();

    const banner = this.#createBannerElement(payload);
    this.#currentBanner = banner;
    container.appendChild(banner);
  }

  /** 警告バナーを閉じる */
  public hideAlert(): void {
    if (this.#currentBanner) {
      this.#currentBanner.remove();
      this.#currentBanner = null;
    }
  }

  /** 安全なDOM生成（XSS脆弱性を防ぐため textContent を徹底） */
  #createBannerElement(payload: WarningPayload): HTMLElement {
    const isDanger = payload.verdict === "likely_fake";
    const riskPercentage = Math.round(payload.riskScore * 100);

    const banner = document.createElement("div");
    banner.className = `waiwai-alert-banner ${isDanger ? "danger" : "warning"}`;
    banner.setAttribute("role", "alert");
    banner.setAttribute("aria-live", "assertive");

    // 上部ドラッグバー（移動できることを視覚的に明示）
    const dragPill = document.createElement("div");
    dragPill.className = "drag-handle-pill";
    dragPill.setAttribute("title", "ドラッグして画面内の好きな場所に移動できます");
    banner.appendChild(dragPill);

    // 1. ヘッダー
    const header = document.createElement("div");
    header.className = "alert-header";

    const titleGroup = document.createElement("div");
    titleGroup.className = "alert-title-group";

    const icon = document.createElement("span");
    icon.className = "alert-badge-icon";
    icon.textContent = isDanger ? "🚨" : "⚠️";

    const title = document.createElement("h4");
    title.className = "alert-title";
    title.textContent = isDanger
      ? "なりすまし・詐欺の可能性が極めて高いです"
      : "不審な発言を検知しました";

    titleGroup.appendChild(icon);
    titleGroup.appendChild(title);

    const closeBtn = document.createElement("button");
    closeBtn.className = "alert-close-btn";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => this.hideAlert());

    header.appendChild(titleGroup);
    header.appendChild(closeBtn);
    banner.appendChild(header);

    // ドラッグ移動を有効化（上部ピルまたはヘッダーを掴んで画面内の好きな位置へ移動可能）
    this.#enableDrag(banner, header, dragPill);

    // 2. 本文ボディ
    const body = document.createElement("div");
    body.className = "alert-body";

    // 危険度メーター
    const meterContainer = document.createElement("div");
    meterContainer.className = "risk-meter-container";

    const riskLabel = document.createElement("span");
    riskLabel.className = "risk-label";
    riskLabel.textContent = "危険度スコア:";

    const barTrack = document.createElement("div");
    barTrack.className = "risk-bar-track";

    const barFill = document.createElement("div");
    barFill.className = "risk-bar-fill";
    barFill.style.width = `${riskPercentage}%`;
    barTrack.appendChild(barFill);

    const riskValue = document.createElement("span");
    riskValue.className = "risk-value";
    riskValue.textContent = `${riskPercentage}%`;

    meterContainer.appendChild(riskLabel);
    meterContainer.appendChild(barTrack);
    meterContainer.appendChild(riskValue);
    body.appendChild(meterContainer);

    // 要約説明
    if (payload.summary) {
      const summary = document.createElement("p");
      summary.className = "alert-summary";
      summary.textContent = payload.summary;
      body.appendChild(summary);
    }

    // 検知された不審発言の引用
    if (payload.suspiciousUtterance) {
      const quoteBox = document.createElement("div");
      quoteBox.className = "detected-utterance-box";

      const boxTag = document.createElement("span");
      boxTag.className = "box-tag";
      boxTag.textContent = "検知された発言:";

      const quote = document.createElement("blockquote");
      quote.className = "quote-text";
      quote.textContent = `「${payload.suspiciousUtterance}」`;

      quoteBox.appendChild(boxTag);
      quoteBox.appendChild(quote);
      body.appendChild(quoteBox);
    }

    // 推奨確認アクション
    if (payload.recommendedActions && payload.recommendedActions.length > 0) {
      const actionBox = document.createElement("div");
      actionBox.className = "recommended-actions";

      const actionTag = document.createElement("span");
      actionTag.className = "action-tag";
      actionTag.textContent = "推奨する確認アクション:";

      const ol = document.createElement("ol");
      for (const action of payload.recommendedActions) {
        const li = document.createElement("li");
        li.textContent = action;
        ol.appendChild(li);
      }

      actionBox.appendChild(actionTag);
      actionBox.appendChild(ol);
      body.appendChild(actionBox);
    }

    banner.appendChild(body);

    // 3. フッター
    const footer = document.createElement("div");
    footer.className = "alert-footer";

    const credit = document.createElement("small");
    credit.textContent = "Mimamori AI Detection";

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "confirm-btn";
    confirmBtn.textContent = "確認しました";
    confirmBtn.addEventListener("click", () => this.hideAlert());

    footer.appendChild(credit);
    footer.appendChild(confirmBtn);
    banner.appendChild(footer);

    return banner;
  }

  /** ドラッグ＆ドロップ移動の制御 */
  #enableDrag(banner: HTMLElement, header: HTMLElement, pill: HTMLElement): void {
    let isDragging = false;
    let startX = 0;
    let startY = 0;
    let initialLeft = 0;
    let initialTop = 0;

    const onMouseDown = (e: MouseEvent) => {
      // 閉じるボタンなどの操作要素がクリックされたときはドラッグを開始しない
      const target = e.target as HTMLElement;
      if (target?.closest("button") || target?.closest("a") || target?.closest("blockquote")) {
        return;
      }

      isDragging = true;
      banner.style.cursor = "grabbing";
      header.style.cursor = "grabbing";
      pill.style.cursor = "grabbing";
      document.body.style.userSelect = "none";
      startX = e.clientX;
      startY = e.clientY;

      const rect = banner.getBoundingClientRect();
      initialLeft = rect.left;
      initialTop = rect.top;

      // 右寄せ固定から絶対座標配置へ切り替え & アニメーション停止
      banner.style.position = "fixed";
      banner.style.left = `${initialLeft}px`;
      banner.style.top = `${initialTop}px`;
      banner.style.right = "auto";
      banner.style.margin = "0";
      banner.style.animation = "none";

      const onMouseMove = (moveEvent: MouseEvent) => {
        if (!isDragging) return;
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;

        // 画面外にはみ出さないよう境界制限
        const maxLeft = window.innerWidth - banner.offsetWidth - 12;
        const maxTop = window.innerHeight - banner.offsetHeight - 12;
        const newLeft = Math.max(12, Math.min(maxLeft, initialLeft + dx));
        const newTop = Math.max(12, Math.min(maxTop, initialTop + dy));

        banner.style.left = `${newLeft}px`;
        banner.style.top = `${newTop}px`;
      };

      const onMouseUp = () => {
        isDragging = false;
        banner.style.cursor = "";
        header.style.cursor = "grab";
        pill.style.cursor = "grab";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      };

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
      e.preventDefault();
    };

    header.addEventListener("mousedown", onMouseDown);
    pill.addEventListener("mousedown", onMouseDown);
  }
}

// シングルトンインスタンス化
const overlayManager = new OverlayManager();

// Service Worker や Popup からのアラートメッセージを受信
chrome.runtime.onMessage.addListener((message: MessageToContentScript) => {
  if (message.type === "SHOW_ALERT") {
    overlayManager.showAlert(message.payload);
  } else if (message.type === "HIDE_ALERT") {
    overlayManager.hideAlert();
  }
});

/**
 * 新規会議開始時にカメラとマイクを自動でオフ（ミュート）にする [Issue #7 強化]
 */
function autoMuteCameraAndMic(): void {
  // すでにこのページロードでミュート処理を完了した場合は再実行しない
  if (sessionStorage.getItem("waiwai_muted_session") === "1") return;

  let attempts = 0;
  const maxAttempts = 30; // 200ms * 30 = 6秒間継続監視

  const interval = setInterval(() => {
    attempts++;

    // 1. マイクがオン状態（「マイクをオフにする」ボタンが存在する、または data-is-muted="false"）の場合にクリック
    const micElements = document.querySelectorAll<HTMLElement>(
      '[aria-label*="マイクをオフ"], [aria-label*="Turn off microphone" i], ' +
      '[data-is-muted="false"], [aria-label*="マイク"][data-is-muted="false"], [aria-label*="microphone" i][data-is-muted="false"]'
    );
    micElements.forEach((el) => {
      const btn = (el.closest('[role="button"]') as HTMLElement) || el;
      btn.click();
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });

    // 2. カメラがオン状態（「カメラをオフにする」ボタンが存在する）の場合にクリック
    const camElements = document.querySelectorAll<HTMLElement>(
      '[aria-label*="カメラをオフ"], [aria-label*="Turn off camera" i]'
    );
    camElements.forEach((el) => {
      const btn = (el.closest('[role="button"]') as HTMLElement) || el;
      btn.click();
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });

    // マイクとカメラの両方がオフ状態になったか確認
    const isMicMuted = document.querySelector(
      '[aria-label*="マイクをオン"], [aria-label*="Turn on microphone" i], [data-is-muted="true"][aria-label*="マイク"]'
    );
    const isCamMuted = document.querySelector(
      '[aria-label*="カメラをオン"], [aria-label*="Turn on camera" i], [data-is-muted="true"][aria-label*="カメラ"]'
    );

    if (isMicMuted && isCamMuted) {
      sessionStorage.setItem("waiwai_muted_session", "1");
      clearInterval(interval);
    } else if (attempts >= maxAttempts) {
      clearInterval(interval);
    }
  }, 200);
}

// 待機画面での自動ミュートを実行
autoMuteCameraAndMic();

console.log("[Mimamori] Content Script injected successfully.");

