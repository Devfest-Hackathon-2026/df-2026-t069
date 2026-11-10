
import type { SessionReport } from "@/types/report";

export class ReportApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ReportApiError";
  }
}

export async function getSessionReport(
  sessionId: string,
  timeoutMs = 15_000,
): Promise<SessionReport> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(
      `/api/reports/${encodeURIComponent(sessionId)}`,
      {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        signal: controller.signal,
        cache: "no-store",
      },
    );

    if (!response.ok) {
      throw new ReportApiError(
        `Report request failed (${response.status})`,
        response.status,
      );
    }

    return (await response.json()) as SessionReport;
  } catch (error) {
    if (error instanceof ReportApiError) {
      throw error;
    }

    throw new ReportApiError(
      error instanceof DOMException && error.name === "AbortError"
        ? "Timeout"
        : "Network error",
    );
  } finally {
    clearTimeout(timer);
  }
}
