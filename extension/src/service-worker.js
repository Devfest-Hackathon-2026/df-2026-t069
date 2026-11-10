/**
 * Service Worker（拡張機能の司令塔）[Issue #8, #9, #17 統合更新]
 *
 * 役割:
 *  - Popup からの開始/停止/状態取得メッセージを処理
 *  - tabCapture の streamId を取得し、Offscreen Document へ中継
 *  - 3秒ごとの音声チャンク (audio-chunk) を受信し、文字起こしAPI (#9) へ送信
 *  - セッション開始・不審検知保存・セッション終了 API (#17) を呼び出し、Firestore に永続化
 *  - 不審発言検知時に Content Script へ SHOW_ALERT メッセージを送信 (#15)
 *  - Meetタブの閉鎖やリロードを検知して自動停止・リソース解放
 *  - 状態（recording / idle）の通知とバッジ制御（プライバシー配慮）
 */

importScripts("analysis-api.js");

const OFFSCREEN_PATH = "src/offscreen.html";
const PERMISSION_PATH = "src/permission.html";
const MEET_URL_PREFIX = "https://meet.google.com/";
const DOWNLOAD_DIR = "mimamori-meet-audio";
const BACKEND_BASE_URL = "https://detector-backend-vaggox657a-an.a.run.app";
const STOP_GRACE_MS = 2000;

function withDeadline(promise, milliseconds) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("通信の完了待ちがタイムアウトしました")), milliseconds);
    }),
  ]).finally(() => clearTimeout(timer));
}

class Badge {
  static recording(tabId) {
    chrome.action.setBadgeBackgroundColor({ color: "#d93025" });
    chrome.action.setBadgeText({ tabId, text: "REC" });
  }

  static error(tabId, text = "ERR") {
    chrome.action.setBadgeBackgroundColor({ color: "#5f6368" });
    chrome.action.setBadgeText({ tabId, text });
  }

  static clear(tabId) {
    chrome.action.setBadgeText({ tabId, text: "" });
  }
}

class OffscreenDocument {
  static async exists() {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [chrome.runtime.getURL(OFFSCREEN_PATH)],
    });
    return contexts.length > 0;
  }

  static async create() {
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ["USER_MEDIA"],
      justification: "Meet タブの音声とマイク音声を録音するため",
    });
  }

  static async close() {
    if (await OffscreenDocument.exists()) {
      await chrome.offscreen.closeDocument();
    }
  }
}

class RecordingController {
  #activeTabId = null;
  #currentSessionId = null;
  #lastSessionId = null;
  #generation = 0;
  #currentContext = null;
  #startPromise = null;
  #stopPromise = null;
  #pendingDownloads = 0;

  #createSessionContext(tabId, sessionId) {
    this.#invalidateCurrentSession();
    this.#generation += 1;
    const ctx = {
      generation: this.#generation,
      sessionId,
      tabId,
      active: true,
      abortController: new AbortController(),
      seenUtteranceIds: new Set(),
      pendingUtterances: [],
      fallbackAlertedIds: new Set(),
      fallbackSavedIds: new Set(),
      transcriptionErrors: [],
      analysisIncomplete: false,
      countedDetectionKeys: new Set(),
      alertedDetectionKeys: new Set(),
      detectionCount: 0,
      isAnalyzing: false,
      analysisPromise: null,
    };
    this.#currentContext = ctx;
    this.#activeTabId = tabId;
    this.#currentSessionId = sessionId;
    this.#lastSessionId = sessionId;
    return ctx;
  }

  #invalidateCurrentSession() {
    this.#generation += 1;
    if (this.#currentContext) {
      this.#currentContext.active = false;
      try {
        this.#currentContext.abortController.abort();
      } catch {}
    }
    this.#currentContext = null;
  }

  #isSessionActive(ctx, sessionId, generation) {
    return Boolean(
      ctx &&
        ctx.active &&
        this.#currentContext === ctx &&
        this.#generation === generation &&
        ctx.generation === generation &&
        this.#currentSessionId === sessionId &&
        ctx.sessionId === sessionId
    );
  }

  #buildDetectionId(sessionId, utteranceId) {
    return `${sessionId}:${utteranceId}`.replace(/\//g, "_");
  }

  #buildAnalysisWindowKey(batch) {
    if (!Array.isArray(batch)) return null;
    const ids = batch
      .map((u) => u?.id)
      .filter((id) => typeof id === "string" && id.length > 0)
      .sort();
    return ids.length > 0 ? `window:${ids.join(",")}` : null;
  }

  #revokeBlobUrl(blobUrl) {
    if (
      blobUrl &&
      typeof URL !== "undefined" &&
      typeof URL.revokeObjectURL === "function"
    ) {
      try {
        URL.revokeObjectURL(blobUrl);
      } catch {}
    }
  }

  async getStatus() {
    const isRecording = await OffscreenDocument.exists();
    return {
      status: isRecording ? "recording" : "idle",
      activeTabId: this.#activeTabId,
      sessionId: this.#currentSessionId,
      lastSessionId: this.#lastSessionId,
      detectionCount: this.#currentContext?.detectionCount ?? 0,
    };
  }

  start(tabOrTabId) {
    if (this.#startPromise) {
      return Promise.reject(new Error("録音開始処理が進行中です"));
    }

    if (this.#stopPromise) {
      return this.#stopPromise.then(() => this.start(tabOrTabId));
    }

    const startPromise = this.#startSession(tabOrTabId);
    this.#startPromise = startPromise;

    return startPromise.finally(() => {
      if (this.#startPromise === startPromise) {
        this.#startPromise = null;
      }
    });
  }

  async #startSession(tabOrTabId) {
    let tabId = null;
    let ctx = null;
    let offscreenCreated = false;

    try {
      if (this.#currentContext) {
        throw new Error("既に録音中のセッションがあります");
      }

      const tab =
        typeof tabOrTabId === "object"
          ? tabOrTabId
          : await chrome.tabs.get(tabOrTabId);

      tabId = tab.id;

      // API通信先の権限があっても、録音対象はMeetに限定する
      if (!tab.url?.startsWith(MEET_URL_PREFIX)) {
        throw new Error("Google Meet のタブで実行してください");
      }

      const sessionId = `meet-${Date.now()}-${this.#generation + 1}-${Math.random().toString(36).substring(2, 7)}`;

      ctx = this.#createSessionContext(tabId, sessionId);

      // ユーザー操作の直後に streamId を取得
      const streamId = await chrome.tabCapture.getMediaStreamId({
        targetTabId: tabId,
      });

      if (!(await OffscreenDocument.exists())) {
        await OffscreenDocument.create();
        offscreenCreated = true;
      }

      await this.#initSessionOnBackend(sessionId);

      const res = await chrome.runtime.sendMessage({
        target: "offscreen",
        type: "start",
        streamId,
      });

      if (!res?.ok) {
        if (res?.error === "mic-permission") {
          await chrome.tabs.create({
            url: chrome.runtime.getURL(PERMISSION_PATH),
          });
        }

        throw new Error(res?.error || "録音の開始に失敗しました");
      }

      // 開始処理中にセッションが無効化されていないか確認
      if (
        this.#currentContext !== ctx ||
        !ctx.active ||
        this.#currentSessionId !== sessionId
      ) {
        throw new Error("録音開始中にセッションが終了しました");
      }

      Badge.recording(tabId);
      this.broadcastStatus("recording");

      console.log(
        `[Mimamori] タブ (${tabId}) で音声キャプチャを開始しました (Session: ${sessionId})`
      );
    } catch (e) {
      if (ctx && this.#currentContext === ctx) {
        this.#invalidateCurrentSession();
        this.#currentSessionId = null;
        this.#activeTabId = null;
      }

      if (offscreenCreated) {
        await OffscreenDocument.close().catch((closeError) => {
          console.warn("[Mimamori] Offscreen終了エラー:", closeError);
        });
      }

      if (ctx && tabId) {
        Badge.error(tabId);
      }

      throw e;
    }
  }

  stop() {
    if (this.#stopPromise) {
      return this.#stopPromise;
    }

    // 停止要求時点で進行中の開始処理を取得
    const startPromise = this.#startPromise;

    this.#stopPromise = (async () => {
      // 録音開始処理が終わるまで待つ
      if (startPromise) {
        await startPromise;
      }

      const ctx = this.#currentContext;

      if (!ctx) {
        return;
      }

      await this.#finishSession(ctx);
    })().finally(() => {
      this.#stopPromise = null;
    });

    return this.#stopPromise;
  }

  async #finishSession(ctx) {
    const sessionId = ctx.sessionId;
    const tabId = ctx.tabId;

    try {
      // 1. 録音を停止し、最終チャンクの送信完了を待つ
      if (!(await OffscreenDocument.exists())) {
        throw new Error("録音処理が見つかりません。セッション終了を中断しました。");
      }

      const deadline = Date.now() + STOP_GRACE_MS;
      try {
        const response = await withDeadline(chrome.runtime.sendMessage({
          target: "offscreen", type: "stop",
        }), Math.max(1, deadline - Date.now()));
        if (response?.ok !== true || response?.analysisIncomplete) {
          ctx.analysisIncomplete = true;
        }
        // 最終チャンクを取り込んだ後、残っている分析だけ期限付きで待つ。
        const analysis = ctx.analysisPromise || (
          ctx.pendingUtterances.length >= 5 ? this.#processAnalysisQueue(ctx) : null
        );
        if (analysis) await withDeadline(analysis, Math.max(1, deadline - Date.now()));
      } catch (error) {
        ctx.analysisIncomplete = true;
        console.warn("[Mimamori] 一部の処理が未完了です:", error.message);
      }
      ctx.analysisIncomplete ||= ctx.transcriptionErrors.length > 0 ||
        ctx.pendingUtterances.length > 0;

      // 5. セッションを無効化
      this.#invalidateCurrentSession();
      this.#currentSessionId = null;

      // 6. バックエンドの終了APIを待つ
      try {
        await withDeadline(this.#endSessionOnBackend(sessionId, ctx.analysisIncomplete), 750);
      } catch (error) {
        ctx.analysisIncomplete = true;
        console.warn("[Mimamori] セッション終了を確認できません:", error.message);
      }

      // 7. 終了通知に失敗しても保存済みのレポートへ進む
      await chrome.tabs.create({
        url: "https" + "://" + "waiwaiwaiwai.web.app" + "/report/" + encodeURIComponent(sessionId) +
          (ctx.analysisIncomplete ? "?incomplete=1" : ""),
      });

      console.log(
        `[Mimamori] セッション終了・レポート表示完了 (${sessionId})`
      );

      this.broadcastStatus("idle");
    } catch (e) {
      console.error("[Mimamori] セッション終了エラー:", e);

      this.broadcastStatus("error", e.message);

      throw e;
    } finally {
      // 成否にかかわらず録音リソースを解放する
      if (this.#currentContext === ctx) {
        this.#invalidateCurrentSession();
      }

      this.#currentSessionId = null;

      // ダウンロード中のBlob URLは生成元のOffscreenを残して保持する。
      if (this.#pendingDownloads === 0) {
        await OffscreenDocument.close().catch((e) => {
          console.warn("[Mimamori] Offscreen終了エラー:", e);
        });
      }

      if (tabId) {
        Badge.clear(tabId);
      }

      this.#activeTabId = null;
    }
  }

  // 3秒ごとのリアルタイム音声チャンク受信 (#8, #9, #12, #17)
  async handleAudioChunk(payload) {
    const ctx = this.#currentContext;
    const sessionId = ctx?.sessionId;
    const generation = ctx?.generation;
    if (!this.#isSessionActive(ctx, sessionId, generation)) {
      this.#revokeBlobUrl(payload?.blobUrl);
      return;
    }

    console.log(
      `[Mimamori] 音声チャンク受信 #${payload.chunkIndex} (${payload.durationSec}s, start: ${payload.startSec}s)`
    );

    try {
      let blob;
      try {
        const res = await fetch(payload.blobUrl);
        if (!this.#isSessionActive(ctx, sessionId, generation)) return;

        blob = await res.blob();
        if (!this.#isSessionActive(ctx, sessionId, generation)) return;
      } finally {
        this.#revokeBlobUrl(payload?.blobUrl);
      }

      const formData = new FormData();
      const audioExtension = payload.mimeType === "audio/wav" ? "wav" : "webm";
      formData.append("audio", blob, `chunk_${payload.chunkIndex}.${audioExtension}`);
      formData.append("start_sec", String(payload.startSec));
      formData.append("session_id", sessionId);

      // バックエンドの文字起こし API を呼び出し (#9)
      const apiRes = await fetch(`${BACKEND_BASE_URL}/api/v1/transcribe`, {
        method: "POST",
        body: formData,
        signal: AbortSignal.any([ctx.abortController.signal, AbortSignal.timeout(10_000)]),
      });
      if (!this.#isSessionActive(ctx, sessionId, generation)) return;

      if (!apiRes.ok) {
        const message = `文字起こしAPIエラー: HTTP ${apiRes.status}`;
        ctx.transcriptionErrors.push(message);
        console.warn(`[Mimamori] ${message}`);
        return;
      }

      const data = await apiRes.json();
      if (!this.#isSessionActive(ctx, sessionId, generation)) return;

      const receivedList = Array.isArray(data.utterances) ? data.utterances : [];
      // プライバシー保護(#6): 会話本文はログに出力せず件数のみ記録
      console.log(`[Mimamori] 発言を受信: ${receivedList.length}件`);

      // 1. 確定発言（is_final === true）のみを未送信キューに蓄積
      // 未確定発言は seenUtteranceIds にも登録せず、後から同じIDで確定した際に取り込めるようにする (#12)
      for (const utt of receivedList) {
        if (
          utt?.is_final === true &&
          utt?.id &&
          typeof utt.text === "string" &&
          utt.text.trim() &&
          !ctx.seenUtteranceIds.has(utt.id)
        ) {
          ctx.seenUtteranceIds.add(utt.id);
          ctx.pendingUtterances.push({
            id: utt.id,
            text: utt.text,
            startSec: typeof utt.start_sec === "number" ? utt.start_sec : (utt.startSec ?? 0),
            speaker: utt.speaker,
          });
        }
      }

      // 2. 未送信の確定発言が5件以上そろったら、5件単位のバッチ分析キューを実行 (#12)
      if (ctx.pendingUtterances.length >= 5) {
        this.#processAnalysisQueue(ctx).catch((error) => {
          ctx.analysisIncomplete = true;
          console.warn("[Mimamori] 分析未完了:", error.message);
        });
      }
    } catch (e) {
      if (e?.name === "AbortError") return;

      ctx.transcriptionErrors.push(e.message || "文字起こし通信エラー");
      console.warn("[Mimamori] 通信エラー:", e.message);
    }
  }

    // 未送信キューから5件ずつ順番に /analyze へ送信する
  async #processAnalysisQueue(ctx) {
    const sessionId = ctx?.sessionId;
    const tabId = ctx?.tabId;
    const generation = ctx?.generation;

    if (!this.#isSessionActive(ctx, sessionId, generation)) {
      return;
    }

    // 実行中の分析があれば、同じPromiseの完了を待つ
    if (ctx.analysisPromise) {
      return ctx.analysisPromise;
    }

    ctx.analysisPromise = (async () => {
      ctx.isAnalyzing = true;

      try {
        while (
          ctx.pendingUtterances.length >= 5 &&
          this.#isSessionActive(ctx, sessionId, generation)
        ) {
          const batch = ctx.pendingUtterances.slice(0, 5);

          const windowUtterances = batch.map((u) => ({
            id: u.id,
            text: u.text,
            startSec: u.startSec,
          }));

          console.log(
            `[Mimamori] /analyze 実行開始 (Session: ${sessionId}, 5件バッチ)`
          );

          const res = await analyzeUtterances({
            sessionId,
            utterances: windowUtterances,
          }, { signal: ctx.abortController.signal });

          if (!this.#isSessionActive(ctx, sessionId, generation)) {
            return;
          }

          if (!res.ok) {
            console.warn(
            `[Mimamori] /analyze 呼び出し失敗 (HTTP ${res.status ?? "network"}, ${res.error})。フォールバック判定を実行します。`
          );

            await this.#runFallbackDetection(
              batch,
              ctx,
              sessionId,
              tabId,
              generation
            );

            // 一時的な通信エラーではキューを残して終了
            break;
          }

          // 成功確認後に5件を消費
          ctx.pendingUtterances.splice(0, 5);

          if (res.skipped) {
            console.log("[Mimamori] /analyze 待機・スキップ (204 No Content)");
            continue;
          }

          const result = res.result;

          console.log(
            `[Mimamori] /analyze 判定完了 (risk_level: ${result?.risk_level})`
          );

          if (
            result &&
            result.is_suspicious === true &&
            (result.risk_level === "high" ||
              result.risk_level === "medium" ||
              result.risk_level === "low")
          ) {
            this.#handleSuspiciousResult(
              result,
              batch,
              ctx,
              sessionId,
              tabId,
              generation
            );
          }
        }
      } catch (e) {
        console.warn("[Mimamori] /analyze キュー処理エラー:", e.message);
        throw e;
      } finally {
        ctx.isAnalyzing = false;
        ctx.analysisPromise = null;
      }
    })();

    return ctx.analysisPromise;
  }

  // Gemini分析結果（is_suspicious=true の low / medium / high）の件数加算・Popup同期と、
  // high のみのリアルタイム画面警告 (#11, #12, #15)
  // ※ 通常のGemini判定結果（low / medium / high）の永続化はバックエンドの /analyze 側で一括保存される (#17, #60, #63)
  #handleSuspiciousResult(result, batch, ctx, sessionId, tabId, generation) {
    if (!this.#isSessionActive(ctx, sessionId, generation)) return;
    if (
      !result ||
      result.is_suspicious !== true ||
      (result.risk_level !== "low" &&
        result.risk_level !== "medium" &&
        result.risk_level !== "high")
    ) {
      return;
    }

    // 同じ分析バッチ・同じ発言の再送（フォールバック後の再送成功を含む）で件数を重複加算しない
    const windowKey = this.#buildAnalysisWindowKey(batch);
    const matches =
      typeof result.quote === "string" && result.quote && Array.isArray(batch)
        ? batch.filter((u) => typeof u?.text === "string" && u.text.includes(result.quote))
        : [];
    const matchedUttKey =
      matches.length === 1 && matches[0]?.id ? `utt:${matches[0].id}` : null;

    const alreadyCounted =
      (windowKey && ctx.countedDetectionKeys.has(windowKey)) ||
      (matchedUttKey && ctx.countedDetectionKeys.has(matchedUttKey));

    if (!alreadyCounted) {
      if (windowKey) ctx.countedDetectionKeys.add(windowKey);
      if (matchedUttKey) ctx.countedDetectionKeys.add(matchedUttKey);
      ctx.detectionCount += 1;
      this.broadcastStatus("recording");
    }

    // 即時警告（SHOW_ALERT）は引き続き high のみ送信
    const isHigh = result.risk_level === "high";
    if (!isHigh || !tabId) return;

    const alreadyAlerted =
      (windowKey && ctx.alertedDetectionKeys.has(windowKey)) ||
      (matchedUttKey && ctx.alertedDetectionKeys.has(matchedUttKey));
    if (alreadyAlerted) return;

    if (windowKey) ctx.alertedDetectionKeys.add(windowKey);
    if (matchedUttKey) ctx.alertedDetectionKeys.add(matchedUttKey);

    chrome.tabs
      .sendMessage(tabId, {
        type: "SHOW_ALERT",
        payload: {
          verdict: "likely_fake",
          riskScore: 0.85,
          summary: result.reason,
          speaker: "相手",
          suspiciousUtterance: result.quote,
          recommendedActions: [result.advice],
          timestamp: Date.now(),
        },
      })
      .catch(() => {});
  }

    // APIキー未設定や通信障害時のセーフティネット
  // 検知結果の保存完了まで待機する
  async #runFallbackDetection(batch, ctx, sessionId, tabId, generation) {
    if (!this.#isSessionActive(ctx, sessionId, generation)) return;

    const savePromises = [];

    let matchedInBatch = false;
    for (const utt of batch) {
      if (
        utt.speaker === "相手" &&
        (utt.text.includes("振込") ||
          utt.text.includes("手数料") ||
          utt.text.includes("至急") ||
          utt.text.includes("暗証番号"))
      ) {
        matchedInBatch = true;
        const detection = {
          id: this.#buildDetectionId(sessionId, utt.id),
          timestamp_sec: utt.startSec,
          speaker: "remote",
          quote: utt.text,
          category: "C1",
          risk_score: 0.85,
          risk_level: "likely_fake",
          reason: "通話相手から金銭または暗証番号の要求が検知されました。",
          advice: "一度通話を終了し、医療機関や相手の代表窓口に直接確認してください。",
        };

        // 保存はタブが閉じられていても実行する
        if (sessionId && !ctx.fallbackSavedIds.has(utt.id)) {
          ctx.fallbackSavedIds.add(utt.id);

          const savePromise = this.#saveDetectionOnBackend(
            sessionId,
            detection
          ).catch((err) => {
            ctx.fallbackSavedIds.delete(utt.id);
            console.warn(
              "[Mimamori] 検知結果保存エラー:",
              err.message
            );
            throw err;
          });

          savePromises.push(savePromise);
        }

        const uttKey = `utt:${utt.id}`;
        if (!ctx.countedDetectionKeys.has(uttKey)) {
          ctx.countedDetectionKeys.add(uttKey);
          ctx.detectionCount += 1;
          this.broadcastStatus("recording");
        }

        // 画面警告はタブが存在する場合だけ送信する
        if (tabId && !ctx.fallbackAlertedIds.has(utt.id)) {
          ctx.fallbackAlertedIds.add(utt.id);
          ctx.alertedDetectionKeys.add(uttKey);
          chrome.tabs
            .sendMessage(tabId, {
              type: "SHOW_ALERT",
              payload: {
                verdict: detection.risk_level,
                riskScore: detection.risk_score,
                summary: detection.reason,
                speaker: "相手",
                suspiciousUtterance: detection.quote,
                recommendedActions: [detection.advice],
                timestamp: Date.now(),
              },
            })
            .catch(() => {});
        }
      }
    }

 if (matchedInBatch) {
      const windowKey = this.#buildAnalysisWindowKey(batch);
      if (windowKey) {
        ctx.countedDetectionKeys.add(windowKey);
        ctx.alertedDetectionKeys.add(windowKey);
      }
    }

    // 成功・失敗を問わず、すべての保存処理が終了するまで待つ
    const results = await Promise.allSettled(savePromises);
    const failed = results.find((result) => result.status === "rejected");

    if (failed) {
      throw failed.reason;
    }
  }

  async #initSessionOnBackend(sessionId) {
  const res = await fetch(`${BACKEND_BASE_URL}/api/v1/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });

  if (!res.ok) {
    throw new Error(`セッション初期化失敗: HTTP ${res.status}`);
  }
}

  async #saveDetectionOnBackend(sessionId, detection) {
    const res = await fetch(
      `${BACKEND_BASE_URL}/api/v1/sessions/${sessionId}/detections`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(detection),
      }
    );

    if (!res.ok) {
      throw new Error(`検知結果保存失敗: HTTP ${res.status}`);
    }
  }

  async #endSessionOnBackend(sessionId, analysisIncomplete = false) {
    const res = await fetch(
      `${BACKEND_BASE_URL}/api/v1/sessions/${encodeURIComponent(sessionId)}/end`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analysis_incomplete: analysisIncomplete }),
        signal: AbortSignal.timeout(750),
      }
    );

    if (!res.ok) {
      throw new Error(`セッション終了API失敗: HTTP ${res.status}`);
    }
  }

  // 通話終了時の最終保存処理
  async handleRecordingReady(blobUrl) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");

    this.#pendingDownloads += 1;
    try {
      const downloadId = await chrome.downloads.download({
        url: blobUrl,
        filename: `${DOWNLOAD_DIR}/meet-audio-${stamp}.webm`,
        saveAs: false,
      });
      await this.waitForDownload(downloadId);
      console.log("[Mimamori] 音声ファイルの保存が完了しました");
    } finally {
      this.#pendingDownloads -= 1;
      if (!this.#currentContext && this.#pendingDownloads === 0) {
        await OffscreenDocument.close();
      }
    }
  }

  // タブが閉じられたときの自動クリーンアップ
  handleTabRemoved(removedTabId) {
    if (this.#activeTabId === removedTabId) {
      console.log(`[Mimamori] 監視中のMeetタブ (${removedTabId}) が閉じられたため自動クリーンアップを実行します`);
      this.stop().catch(() => {});
    }
  }

  broadcastStatus(status, errorMessage = null) {
    chrome.runtime
      .sendMessage({
        type: "STATUS_UPDATE",
        status,
        activeTabId: this.#activeTabId,
        sessionId: this.#currentSessionId,
        lastSessionId: this.#lastSessionId,
        detectionCount: this.#currentContext?.detectionCount ?? 0,
        errorMessage,
      })
      .catch(() => {});
  }

  waitForDownload(downloadId) {
    return new Promise((resolve, reject) => {
      let settled = false;

      const timeoutId = setTimeout(() => {
        finish(new Error("録音ファイルの保存確認がタイムアウトしました"));
      }, 60000);

      const cleanup = () => {
        clearTimeout(timeoutId);
        chrome.downloads.onChanged.removeListener(listener);
      };

      const finish = (error) => {
        if (settled) return;

        settled = true;
        cleanup();

        if (error) {
          reject(error);
        } else {
          resolve();
        }
      };

      const checkState = (state) => {
        if (state === "complete") {
          finish();
        } else if (state === "interrupted") {
          finish(new Error("録音ファイルの保存が中断されました"));
        }
      };

      const listener = (delta) => {
        if (delta.id !== downloadId || !delta.state) return;
        checkState(delta.state.current);
      };

      // ダウンロード完了イベントを取りこぼさないよう先に登録
      chrome.downloads.onChanged.addListener(listener);

      // リスナー登録前後に完了していた場合も確認
      chrome.downloads.search({ id: downloadId })
        .then((items) => {
          if (!items.length) {
            finish(new Error("ダウンロード状態を確認できません"));
            return;
          }

          checkState(items[0].state);
        })
        .catch((error) => {
          finish(error);
        });
    });
  }
}

const controller = new RecordingController();

// メッセージハンドラ
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 自分の拡張以外からのメッセージは無視する (セキュリティ)
  if (sender.id !== chrome.runtime.id) return;

  // 1. Popup からのメッセージ
  if (msg.type === "GET_STATUS") {
    controller.getStatus().then((status) => {
      sendResponse({ type: "STATUS_UPDATE", ...status });
    });
    return true;
  }

  if (msg.type === "START_RECORDING") {
    controller
      .start(msg.targetTabId)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
        controller.broadcastStatus("error", err.message);
      });
    return true;
  }

  if (msg.type === "STOP_RECORDING") {
    controller
      .stop()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => {
        sendResponse({ ok: false, error: err.message });
        controller.broadcastStatus("error", err.message);
      });

    return true;
  }

  // 2. Offscreen Document からの通知
  if (msg?.target === "service-worker") {
    if (msg.type === "audio-chunk") {
      controller
        .handleAudioChunk(msg.payload)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => {
          console.warn("[Mimamori] 音声チャンク処理エラー:", err.message);
          sendResponse({ ok: false, error: err.message });
        });
      return true;
    } else if (msg.type === "recording-ready") {
      const sessionAtReady = controller.getStatus();
      controller
        .handleRecordingReady(msg.blobUrl)
        .then(async () => {
          // Offscreenへ保存完了を返してから停止処理を開始する
          sendResponse({ ok: true });

          const original = await sessionAtReady;
          const current = await controller.getStatus();
          // 過去の録音保存が遅れて完了しても、新しい通話を停止しない。
          if (original.sessionId && original.sessionId === current.sessionId) {
            controller.stop().catch((err) => {
              console.error("[Mimamori] 自動停止エラー:", err);
            });
          }
        })
        .catch(async (err) => {
          console.error("[Mimamori] 保存エラー:", err);
          sendResponse({ ok: false, error: err.message });
          const original = await sessionAtReady;
          const current = await controller.getStatus();
          if (original.sessionId && original.sessionId === current.sessionId) {
            controller.broadcastStatus("error", err.message);
          }
        });

      return true;
    } else if (msg.type === "recording-error") {
      console.error("[Mimamori] 録音エラー:", msg.error);

      controller.broadcastStatus("error", msg.error);

      controller.stop().catch((err) => {
        console.error("[Mimamori] 録音エラー後の停止処理:", err);
      });
    }
  }

  // 3. /analyze への認証付き送信 (#10)
  if (msg.type === "analyze") {
    // content scriptからはMeet上のメッセージだけを受け付ける。
    if (sender.tab && !sender.url?.startsWith(MEET_URL_PREFIX)) return;
    analyzeUtterances(msg.payload).then(sendResponse);
    return true;
  }
});

// タブ閉鎖・URL変更の監視
chrome.tabs.onRemoved.addListener((tabId) => {
  controller.handleTabRemoved(tabId);
});

// ツールバーアイコンの確実な適用
function applyExtensionIcon() {
  try {
    chrome.action.setIcon({
      path: {
        16: "icons/icon16.png",
        24: "icons/icon24.png",
        32: "icons/icon32.png",
        48: "icons/icon48.png",
        128: "icons/icon128.png",
      },
    }).catch(() => {});
  } catch {}
}

applyExtensionIcon();
if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(applyExtensionIcon);
}

// インストール時にマイク許可ページを開く
chrome.runtime.onInstalled.addListener(({ reason }) => {
  applyExtensionIcon();
  if (reason === chrome.runtime.OnInstalledReason.INSTALL) {
    chrome.tabs.create({ url: chrome.runtime.getURL(PERMISSION_PATH) });
  }
});
