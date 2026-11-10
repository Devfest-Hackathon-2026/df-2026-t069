/**
 * Offscreen document（音声キャプチャとチャンク送信処理）[Issue #8]
 *
 * 役割:
 *  - chrome.tabCapture のストリーム（相手の声）とマイク（自分の声）をステレオ合成
 *  - 相手の音声を ctx.destination 経由でスピーカーへ流し直し、ユーザーの通話を阻害しない
 *  - 3秒ごとに音声チャンク（WebM/Opus）を生成し、Service Worker へ定期送信（ストリーミング）
 *  - 通話終了（タブ切断・閉鎖）時の自動クリーンアップとリソース解放
 */

const MIME_TYPE = "audio/webm;codecs=opus";
const CHUNK_INTERVAL_MS = 3000; // 3秒ごとに文字起こしへ送信

class StereoCallRecorder {
  #ctx = null;
  #tabStream = null;
  #micStream = null;
  #recorder = null;
  #pcmProcessor = null;
  #pcmSilentGain = null;
  #pcmBlocks = [];
  #pcmBufferedFrames = 0;
  #pcmChunkFrames = 0;
  #pcmFramesSent = 0;
  #allChunks = []; // 最終保存用の全チャンク
  #chunkIndex = 0;
  #startTime = 0;
  #intervalTimer = null;
  #pendingChunkSends = new Set();
  #stopPromise = null;
  #generation = 0;
  analysisIncomplete = false;

  get isRecording() {
    return this.#recorder?.state === "recording";
  }

  async start(streamId) {
    // 既存の録音リソースがある場合は、破棄せず二重開始を拒否する
    if (this.#recorder || this.#tabStream || this.#micStream || this.#ctx) {
      throw new Error("既に音声キャプチャが開始されています");
    }

    const generation = ++this.#generation;
    try {
      this.#tabStream = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
        video: false,
      });

      this.#micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });

      const ctx = new AudioContext();
      this.#ctx = ctx;

      const tabSource = ctx.createMediaStreamSource(this.#tabStream);
      const micSource = ctx.createMediaStreamSource(this.#micStream);

      // 1) 相手の声をスピーカーへ流し直す
      tabSource.connect(ctx.destination);

      // 2) L=自分、R=相手としてステレオ合成
      const merger = ctx.createChannelMerger(2);
      this.#toMono(ctx, micSource).connect(merger, 0, 0);
      this.#toMono(ctx, tabSource).connect(merger, 0, 1);

      const destination = ctx.createMediaStreamDestination();
      destination.channelCount = 2;
      merger.connect(destination);

      // 独立した3秒PCMチャンクを生成する
      this.#pcmChunkFrames = Math.round(
        ctx.sampleRate * CHUNK_INTERVAL_MS / 1000
      );
      this.#pcmFramesSent = 0;
      this.#pcmBlocks = [];
      this.#pcmBufferedFrames = 0;

      this.#pcmProcessor = ctx.createScriptProcessor(4096, 2, 2);
      this.#pcmSilentGain = ctx.createGain();
      this.#pcmSilentGain.gain.value = 0;
      this.#pcmProcessor.onaudioprocess = (event) => {
        this.#handlePcmAudio(event);
      };
      merger.connect(this.#pcmProcessor);
      this.#pcmProcessor.connect(this.#pcmSilentGain);
      this.#pcmSilentGain.connect(ctx.destination);

      this.#allChunks = [];
      this.#chunkIndex = 0;
      this.#startTime = Date.now();
      this.analysisIncomplete = false;
      this.#pendingChunkSends.clear();
      this.#stopPromise = null;

      this.#recorder = new MediaRecorder(destination.stream, {
        mimeType: MIME_TYPE,
      });

      this.#recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          this.#allChunks.push(e.data);

        }
      };

      this.#tabStream.getAudioTracks()[0]?.addEventListener("ended", () => {
        console.log(
          "[Mimamori] Meetタブが閉じられたか通話が終了したため録音を自動停止します",
        );

        finishRecording().catch((e) =>
          generation !== this.#generation ? undefined : chrome.runtime
            .sendMessage({
              target: "service-worker",
              type: "recording-error",
              error: String(e),
            })
            .catch(() => {}),
        );
      });

      this.#recorder.start(CHUNK_INTERVAL_MS);
      console.log("[Mimamori] 音声キャプチャと3秒チャンク分割を開始しました");
    } catch (e) {
      this.dispose();
      throw e;
    }
  }

  #handlePcmAudio(event) {
    try {
      const left = new Float32Array(event.inputBuffer.getChannelData(0));
      const right = new Float32Array(event.inputBuffer.getChannelData(1));

      this.#pcmBlocks.push({ left, right, offset: 0 });
      this.#pcmBufferedFrames += left.length;

      while (this.#pcmBufferedFrames >= this.#pcmChunkFrames) {
        this.#sendPcmFrames(this.#pcmChunkFrames);
      }
    } catch (error) {
      this.analysisIncomplete = true;
      console.error("[Mimamori] PCM生成エラー:", error);
    }
  }

  #sendPcmFrames(frameCount) {
    const sampleRate = this.#ctx.sampleRate;
    const left = new Float32Array(frameCount);
    const right = new Float32Array(frameCount);

    let written = 0;

    while (written < frameCount) {
      const block = this.#pcmBlocks[0];
      const available = block.left.length - block.offset;
      const count = Math.min(available, frameCount - written);

      left.set(
        block.left.subarray(block.offset, block.offset + count),
        written
      );
      right.set(
        block.right.subarray(block.offset, block.offset + count),
        written
      );

      block.offset += count;
      written += count;

      if (block.offset === block.left.length) {
        this.#pcmBlocks.shift();
      }
    }

    this.#pcmBufferedFrames -= frameCount;

    const startSec = this.#pcmFramesSent / sampleRate;
    this.#pcmFramesSent += frameCount;

    const blob = this.#encodeWav(left, right, sampleRate);
    const pending = this.#emitChunk(
      blob,
      "audio/wav",
      startSec,
      frameCount / sampleRate
    );

    this.#pendingChunkSends.add(pending);
    pending
      .catch(() => { this.analysisIncomplete = true; })
      .finally(() => this.#pendingChunkSends.delete(pending));
  }

  #flushPcmRemainder() {
    if (this.#pcmBufferedFrames > 0) {
      this.#sendPcmFrames(this.#pcmBufferedFrames);
    }
  }

  #encodeWav(left, right, sampleRate) {
    const frames = left.length;
    const channels = 2;
    const bytesPerSample = 2;
    const dataSize = frames * channels * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset, value) => {
      for (let i = 0; i < value.length; i++) {
        view.setUint8(offset + i, value.charCodeAt(i));
      }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * channels * bytesPerSample, true);
    view.setUint16(32, channels * bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < frames; i++) {
      for (const sample of [left[i], right[i]]) {
        const clamped = Math.max(-1, Math.min(1, sample));
        const pcm = clamped < 0
          ? clamped * 0x8000
          : clamped * 0x7fff;
        view.setInt16(offset, pcm, true);
        offset += 2;
      }
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  async #emitChunk(blobData, mimeType, startSec, durationSec) {
    const blobUrl = URL.createObjectURL(blobData);
    this.#chunkIndex += 1;

    try {
      const response = await chrome.runtime.sendMessage({
        target: "service-worker",
        type: "audio-chunk",
        payload: {
          chunkIndex: this.#chunkIndex,
          startSec,
          durationSec,
          blobUrl,
          mimeType,
          timestamp: Date.now(),
        },
      });

      if (response?.ok === false) {
        throw new Error("Service Workerで音声チャンク処理に失敗しました");
      }
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
  }

  stop() {
    if (this.#stopPromise) return this.#stopPromise;

    this.#stopPromise = new Promise((resolve, reject) => {
      if (!this.#recorder) {
        reject(new Error("録音データがありません。録音停止処理を中断しました。"));
        return;
      }

      if (this.#recorder.state === "inactive") {
        reject(
          new Error("録音が既に停止しています。録音ファイルの保存を確認できないため、レポートは開きません。")
        );
        return;
      }
      this.#recorder.onstop = async () => {
        try {
          const fullBlob = new Blob(this.#allChunks, { type: MIME_TYPE });
          this.#flushPcmRemainder();

          // 入力デバイスは通信を待たず解放する。
          this.dispose();
          let timer;
          const results = await Promise.race([
            Promise.allSettled([...this.#pendingChunkSends]),
            new Promise(resolve => { timer = setTimeout(() => resolve(null), 1000); }),
          ]).finally(() => clearTimeout(timer));
          if (!results || results.some(result => result.status === "rejected")) {
            this.analysisIncomplete = true;
          }

          resolve(fullBlob);
        } catch (e) {
          this.dispose();
          reject(e);
        }
      };

      this.#recorder.stop();
    });

    return this.#stopPromise;
  }

  dispose() {
    if (this.#pcmProcessor) {
      this.#pcmProcessor.onaudioprocess = null;
      this.#pcmProcessor.disconnect();
    }
    this.#pcmSilentGain?.disconnect();
    this.#pcmProcessor = null;
    this.#pcmSilentGain = null;
    this.#pcmBlocks = [];
    this.#pcmBufferedFrames = 0;
    if (this.#intervalTimer) clearInterval(this.#intervalTimer);
    this.#tabStream?.getTracks().forEach((t) => t.stop());
    this.#micStream?.getTracks().forEach((t) => t.stop());
    this.#ctx?.close();
    this.#tabStream = null;
    this.#micStream = null;
    this.#ctx = null;
    this.#recorder = null;
    this.#allChunks = [];
    this.#intervalTimer = null;
  }

  #toMono(ctx, source) {
    const mono = ctx.createGain();
    mono.channelCount = 1;
    mono.channelCountMode = "explicit";
    mono.channelInterpretation = "speakers";
    source.connect(mono);
    return mono;
  }
}

const recorder = new StereoCallRecorder();
let finishPromise = null;

async function finishRecording() {
  if (finishPromise) return finishPromise;

  finishPromise = (async () => {
    const blob = await recorder.stop();
    if (!blob) return;

    const blobUrl = URL.createObjectURL(blob);

    try {
      const response = await chrome.runtime.sendMessage({
        target: "service-worker",
        type: "recording-ready",
        blobUrl,
      });

      if (response?.ok !== true) {
        throw new Error(response?.error || "Service Workerが録音完了を確認できませんでした");
      }
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
  })();

  return finishPromise;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || msg?.target !== "offscreen") return;

  if (msg.type === "start") {
    finishPromise = null;

    recorder
      .start(msg.streamId)
      .then(() => sendResponse({ ok: true }))
      .catch((e) => {
        const error = e?.name === "NotAllowedError" ? "mic-permission" : String(e);
        sendResponse({ ok: false, error });
      });
    return true;
  }

  if (msg.type === "stop") {
    finishRecording()
      .then(() => sendResponse({ ok: true, analysisIncomplete: recorder.analysisIncomplete }))
      .catch((e) => {
        sendResponse({ ok: false, error: String(e) });
        // 手動停止の失敗は応答で通知済み。遅れて次の通話へエラーを送らない。
      });
    return true;
  }
});
