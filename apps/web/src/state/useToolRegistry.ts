import { useCallback, useEffect, useState } from "react";

import type { ClientPort } from "../api/client";
import { errorSentence } from "../errors";
import type { ToolDescriptor } from "../types";

export interface ToolRegistryState {
  contractVersion: string | null;
  byName: ReadonlyMap<string, ToolDescriptor>;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useToolRegistry(client: ClientPort): ToolRegistryState {
  const [contractVersion, setContractVersion] = useState<string | null>(null);
  const [byName, setByName] = useState<ReadonlyMap<string, ToolDescriptor>>(
    () => new Map(),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const registry = await client.getTools();
      setContractVersion(registry.contract_version);
      setByName(new Map(registry.tools.map((tool) => [tool.name, tool])));
      setError(null);
    } catch (caught) {
      setContractVersion(null);
      setByName(new Map());
      setError(errorSentence(caught));
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { contractVersion, byName, loading, error, reload };
}
