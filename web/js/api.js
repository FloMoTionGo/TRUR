// Same-origin JSON fetch wrapper. Errors come back as {"error": {"code", "message"}} (docs/architecture.md §3).

export class ApiError extends Error {
  constructor(status, code, message, retryAfter) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

async function request(method, path, body) {
  const init = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (data && data.error) || {};
    const retry = Number(response.headers.get("Retry-After")) || undefined;
    throw new ApiError(response.status, error.code || "http_error", error.message || "Something went wrong.", retry);
  }
  return data;
}

export const api = {
  me: () => request("GET", "/api/users/me"),
  register: (nickname) => request("POST", "/api/users", { nickname }),
  currentGame: (mode) => request("GET", `/api/games/current?mode=${encodeURIComponent(mode)}`),
  newGame: (mode) => request("POST", "/api/games", { mode }),
  submit: (gameId, tiles) => request("POST", `/api/games/${encodeURIComponent(gameId)}/submit`, { tiles }),
  end: (gameId) => request("POST", `/api/games/${encodeURIComponent(gameId)}/end`, {}),
  events: (events) => request("POST", "/api/events", { events }),
  answers: (day) => request("GET", `/api/daily/${encodeURIComponent(day)}/answers`),
};
