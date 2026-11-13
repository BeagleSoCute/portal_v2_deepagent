import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, messageOf, Redirecting, type AgentRow } from './api';

type Agents = {
  /** null until the first load finishes. */
  agents: AgentRow[] | null;
  problem: string;
  reload: () => Promise<void>;
};

const AgentsContext = createContext<Agents>({ agents: null, problem: '', reload: async () => {} });

// The sidebar and the open agent show the same list, so it is loaded once and shared.
export function AgentsProvider({ children }: { children: ReactNode }) {
  const [agents, setAgents] = useState<AgentRow[] | null>(null);
  const [problem, setProblem] = useState('');

  const reload = useCallback(async () => {
    try {
      setAgents(await api.agents());
      setProblem('');
    } catch (error) {
      if (!(error instanceof Redirecting)) setProblem(messageOf(error));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return <AgentsContext.Provider value={{ agents, problem, reload }}>{children}</AgentsContext.Provider>;
}

export const useAgents = () => useContext(AgentsContext);
