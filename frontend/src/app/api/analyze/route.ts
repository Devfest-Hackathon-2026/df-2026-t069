const corsHeaders = (origin: string) => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "600",
  Vary: "Origin",
});

function getAllowedOrigin(request: Request): string | null {
  const configuredOrigin = process.env.EXTENSION_ORIGIN;
  const requestOrigin = request.headers.get("origin");

  if (!configuredOrigin || !requestOrigin || requestOrigin !== configuredOrigin) {
    return null;
  }

  return configuredOrigin;
}

export async function OPTIONS(request: Request) {
  const origin = getAllowedOrigin(request);

  if (!origin) {
    return new Response(null, { status: 403 });
  }

  return new Response(null, {
    status: 204,
    headers: corsHeaders(origin),
  });
}

export async function POST(request: Request) {
  const origin = getAllowedOrigin(request);

  if (!origin) {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }

  const apiKey = process.env.BACKEND_API_KEY;
  if (!apiKey?.trim()) {
    return Response.json(
      { error: "backend-auth-not-configured" },
      { status: 503, headers: corsHeaders(origin) },
    );
  }

  const backendUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8080";

  try {
    const body = await request.text();

    // 音声そのものではなく、分析対象の発言テキストだけを受け付ける。
    if (body.length > 64_000) {
      return Response.json(
        { error: "request-too-large" },
        { status: 413, headers: corsHeaders(origin) },
      );
    }

    try {
      JSON.parse(body);
    } catch {
      return Response.json(
        { error: "invalid-json" },
        { status: 400, headers: corsHeaders(origin) },
      );
    }

    const upstream = await fetch(`${backendUrl.replace(/\/+$/, "")}/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });

    if (upstream.status === 204) {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(origin),
      });
    }

    if (!upstream.ok) {
      // バックエンドのエラー本文や発言内容は拡張機能へ返さない。
      return Response.json(
        { error: "analysis-upstream-failed" },
        { status: upstream.status, headers: corsHeaders(origin) },
      );
    }

    const result = await upstream.json();

    return Response.json(result, {
      status: 200,
      headers: {
        ...corsHeaders(origin),
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { error: "analysis-proxy-failed" },
      { status: 502, headers: corsHeaders(origin) },
    );
  }
}
