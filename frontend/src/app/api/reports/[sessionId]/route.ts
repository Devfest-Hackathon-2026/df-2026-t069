
interface RouteContext {
  params: Promise<{ sessionId: string }>;
}

const BACKEND_API_URL = (
  process.env.BACKEND_API_URL ?? "http://127.0.0.1:8080"
).replace(/\/+$/, "");

export async function GET(
  _request: Request,
  { params }: RouteContext,
) {
  const { sessionId } = await params;

  if (!sessionId?.trim()) {
    return Response.json(
      { error: "invalid-session-id" },
      { status: 400 },
    );
  }

  try {
    const upstream = await fetch(
      `${BACKEND_API_URL}/api/v1/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: AbortSignal.timeout(15_000),
      },
    );

    if (upstream.status === 404) {
      return Response.json(
        { error: "session-not-found" },
        { status: 404 },
      );
    }

    if (!upstream.ok) {
      return Response.json(
        { error: "report-upstream-failed" },
        { status: 502 },
      );
    }

    const report = await upstream.json();

    return Response.json(report, {
      status: 200,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const isTimeout =
      error instanceof Error && error.name === "TimeoutError";

    return Response.json(
      {
        error: isTimeout ? "report-timeout" : "report-proxy-failed",
      },
      { status: isTimeout ? 504 : 502 },
    );
  }
}
