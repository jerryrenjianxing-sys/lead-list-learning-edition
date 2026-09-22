let token = "";
export async function connect() {
  const response = await fetch("/api/v1/session", {
    credentials: "same-origin",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("无法连接本机服务");
  token = (await response.json()).token;
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  timeoutMs?: number,
): Promise<T> {
  if (!token) await connect();
  const requestId = crypto.randomUUID();
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch("/api/v1" + path, {
        method,
        signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
        credentials: "same-origin",
        headers: {
          Authorization: `Bearer ${token}`,
          "Idempotency-Key": requestId,
          ...(body instanceof FormData
            ? {}
            : { "Content-Type": "application/json" }),
        },
        body:
          body === undefined
            ? undefined
            : body instanceof FormData
              ? body
              : JSON.stringify(body),
      });
      if (response.status === 401 && attempt === 0) {
        await connect();
        continue;
      }
      const result = await response.json();
      if (!response.ok)
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : JSON.stringify(result.detail),
        );
      return result as T;
    } catch (error) {
      if (error instanceof TypeError && attempt === 0) continue;
      throw error;
    }
  }
  throw new Error("连接已失效，请重新打开软件");
}
