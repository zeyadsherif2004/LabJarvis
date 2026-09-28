import { useCallback, useEffect, useRef, useState } from "react";

import type { ClientPort } from "../../api/client";
import { errorSentence } from "../../errors";
import type { ProtocolSummary, SessionSummary } from "../../types";

export interface DashboardDiscovery {
  protocols: ProtocolSummary[];
  sessions: SessionSummary[];
  loading: boolean;
  authoritative: boolean;
  error: string | null;
  search: (filters?: DashboardFilters) => Promise<void>;
}

export interface DashboardFilters {
  query?: string;
  label?: string;
  protocolVersionId?: string;
  state?: string;
  createdFrom?: string;
  createdTo?: string;
  archived?: boolean;
}

export function useDashboardDiscovery(
  client: ClientPort,
  active: boolean,
): DashboardDiscovery {
  const [protocols, setProtocols] = useState<ProtocolSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [authoritative, setAuthoritative] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);

  const search = useCallback(
    async (filters: DashboardFilters = {}) => {
      const generation = requestGeneration.current + 1;
      requestGeneration.current = generation;
      activeRequest.current?.abort();
      const controller = new AbortController();
      activeRequest.current = controller;
      setLoading(true);
      setAuthoritative(false);

      try {
        const normalizedQuery = filters.query?.trim() ?? "";
        const normalizedLabel = filters.label?.trim() ?? "";
        const normalizedProtocolVersionId = filters.protocolVersionId?.trim() ?? "";
        const [foundProtocols, foundSessions] = await Promise.all([
          client.listProtocols(
            {
              ...(normalizedQuery ? { query: normalizedQuery } : {}),
              ...(filters.archived !== undefined ? { archived: filters.archived } : {}),
              ...(filters.createdFrom ? { createdFrom: filters.createdFrom } : {}),
              ...(filters.createdTo ? { createdTo: filters.createdTo } : {}),
            },
            controller.signal,
          ),
          client.listSessions(
            {
              ...(normalizedQuery ? { query: normalizedQuery } : {}),
              ...(normalizedLabel ? { label: normalizedLabel } : {}),
              ...(normalizedProtocolVersionId ? { protocolVersionId: normalizedProtocolVersionId } : {}),
              ...(filters.state ? { state: filters.state } : {}),
              ...(filters.createdFrom ? { createdFrom: filters.createdFrom } : {}),
              ...(filters.createdTo ? { createdTo: filters.createdTo } : {}),
              protocolArchived: filters.archived ?? false,
            },
            controller.signal,
          ),
        ]);
        if (requestGeneration.current !== generation) return;
        setProtocols(foundProtocols);
        setSessions(foundSessions);
        setError(null);
        setAuthoritative(true);
      } catch (caught) {
        if (requestGeneration.current !== generation) return;
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(errorSentence(caught));
      } finally {
        if (requestGeneration.current === generation) {
          activeRequest.current = null;
          setLoading(false);
        }
      }
    },
    [client],
  );

  useEffect(() => {
    if (!active) return;
    void search();
    return () => {
      requestGeneration.current += 1;
      activeRequest.current?.abort();
      activeRequest.current = null;
    };
  }, [active, search]);

  return { protocols, sessions, loading, authoritative, error, search };
}
