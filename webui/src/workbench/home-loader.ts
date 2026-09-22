export async function fetchLocalApi(
  url: string,
  options: RequestInit = {},
  timeout = 15000,
) {
  const controller = new AbortController(),
    timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}
export async function loadSkillMarkdown(load: () => Promise<Response>) {
  const response = await load();
  if (!response.ok) throw new Error("Connection instructions unavailable");
  return response.text();
}
