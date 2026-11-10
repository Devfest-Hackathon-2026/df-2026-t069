/**
 * ブラウザのマイク/カメラから一定時間録音・録画するサービス。[Role2]
 * メディアは端末メモリ上の Blob としてのみ扱い、ローカル保存はしない。
 *
 * TODO(Role2): 通話中の連続監視（数秒ごとのチャンク送信）への拡張
 */
export type CaptureKind = "audio" | "video";

export class MediaCaptureService {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;

  async start(kind: CaptureKind): Promise<MediaStream> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: kind === "video",
    });
    return this.stream;
  }

  /** start() 後に呼ぶ。durationMs 経過で録音を止め Blob を返す */
  record(durationMs: number): Promise<Blob> {
    if (!this.stream) return Promise.reject(new Error("Capture not started"));
    const chunks: BlobPart[] = [];
    const recorder = new MediaRecorder(this.stream);
    this.recorder = recorder;

    return new Promise((resolve, reject) => {
      recorder.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      recorder.onerror = () => reject(new Error("Recording failed"));
      recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType }));
      recorder.start();
      setTimeout(() => recorder.state !== "inactive" && recorder.stop(), durationMs);
    });
  }

  /** マイク/カメラを確実に解放する（ブラウザの録音インジケータを消す） */
  stop(): void {
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
  }
}

