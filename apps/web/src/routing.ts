/**
 * URL-addressable application routes.
 *
 * The active protocol and session live in the URL rather than only in React
 * state. That is what makes a reload, a bookmark, and a shared link all land on
 * the same screen, and it means the app has one place to read "what am I looking
 * at" from after a refresh instead of reconstructing it from memory.
 *
 * Hash routing is used deliberately: the app is served as static files behind
 * nginx in the container image, and hash routes need no server rewrite rules.
 */

export type Route =
  | { name: "dashboard" }
  | { name: "import" }
  | { name: "protocol"; versionId: string }
  | { name: "session"; sessionId: string }
  | { name: "notebook"; sessionId: string }
  | { name: "data"; sessionId?: string }
  | { name: "diagnostics" }
  | { name: "settings" };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "").trim();
  if (path === "" || path === "dashboard") return { name: "dashboard" };
  if (path === "import") return { name: "import" };
  if (path === "diagnostics") return { name: "diagnostics" };
  if (path === "settings") return { name: "settings" };
  if (path === "data") return { name: "data" };

  const [section, identifier, subsection] = path.split("/");
  if (section === "protocols" && identifier) {
    return { name: "protocol", versionId: identifier };
  }
  if (section === "sessions" && identifier) {
    return subsection === "notebook"
      ? { name: "notebook", sessionId: identifier }
      : { name: "session", sessionId: identifier };
  }
  if (section === "data" && identifier === "sessions" && subsection) {
    return { name: "data", sessionId: subsection };
  }
  return { name: "dashboard" };
}

export function routeToHash(route: Route): string {
  switch (route.name) {
    case "dashboard":
      return "#/";
    case "import":
      return "#/import";
    case "diagnostics":
      return "#/diagnostics";
    case "settings":
      return "#/settings";
    case "data":
      return route.sessionId ? `#/data/sessions/${route.sessionId}` : "#/data";
    case "protocol":
      return `#/protocols/${route.versionId}`;
    case "session":
      return `#/sessions/${route.sessionId}`;
    case "notebook":
      return `#/sessions/${route.sessionId}/notebook`;
  }
}

/** The session this route refers to, if any. Used to decide what to rehydrate. */
export function sessionIdOf(route: Route): string | null {
  return route.name === "session" || route.name === "notebook" || route.name === "data"
    ? route.sessionId ?? null
    : null;
}
