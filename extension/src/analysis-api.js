/** Service Worker専用。APIキーはNext.jsサーバー側で付与する。 */
async function analyzeUtterances(payload, { signal } = {}) {
  try {
    const response = await fetch("https://waiwaiwaiwai.web.app/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "omit",
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(30_000)])
        : AbortSignal.timeout(30_000),
    });

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: "api-request-failed",
      };
    }

    if (response.status === 204) {
      return { ok: true, skipped: true };
    }

    return { ok: true, result: await response.json() };
  } catch {
    return { ok: false, error: "api-network-error" };
  }
}