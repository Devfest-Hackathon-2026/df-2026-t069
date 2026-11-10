/**
 * バックエンド検知APIのクライアント。[Role2]
 * UI からは fetch を直接呼ばず、このクラス経由にすることで
 * エンドポイント変更・リトライ・タイムアウト等の関心事を1か所に閉じ込める。
 */
import type { DetectionResponse } from "@/types/detection";

export class DetectionApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "DetectionApiError";
  }
}

export class DetectionApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly timeoutMs = 30_000,
  ) {}

  async detect(media: Blob, filename: string, context?: string): Promise<DetectionResponse> {
    const form = new FormData();
    form.append("media", media, filename);
    if (context) form.append("context", context);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/detect`, {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      if (!res.ok) {
        throw new DetectionApiError(`Detection failed (${res.status})`, res.status);
      }
      return (await res.json()) as DetectionResponse;
    } catch (e) {
      if (e instanceof DetectionApiError) throw e;
      throw new DetectionApiError(
        e instanceof DOMException && e.name === "AbortError" ? "Timeout" : "Network error",
      );
    } finally {
      clearTimeout(timer);
    }
  }
}

export const detectionApi = new DetectionApiClient(
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080",
);

