import { useCallback, useEffect, useState } from "react";

import { parseRoute, routeToHash, type Route } from "../routing";

/** Keep the selected resource in a reloadable, bookmarkable hash route. */
export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() =>
    parseRoute(globalThis.location?.hash ?? ""),
  );

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(globalThis.location.hash));
    globalThis.addEventListener("hashchange", onChange);
    return () => globalThis.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: Route) => {
    const hash = routeToHash(next);
    if (globalThis.location.hash === hash) {
      setRoute(next);
      return;
    }
    globalThis.location.hash = hash;
  }, []);

  return [route, navigate];
}
