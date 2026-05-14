import { useState } from "react";
import type { ApiConfig } from "../api";
import { fetchHealth } from "../api";

interface Props {
  config: ApiConfig;
  onChange: (config: ApiConfig) => void;
}

export function ApiConfigBar({ config, onChange }: Props): React.JSX.Element {
  const [status, setStatus] = useState<{ kind: "idle" | "ok" | "error"; message: string }>({
    kind: "idle",
    message: "",
  });

  async function ping(): Promise<void> {
    setStatus({ kind: "idle", message: "Comprobando..." });
    try {
      const health = await fetchHealth(config);
      setStatus({ kind: "ok", message: `OK · v${health.version}` });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      setStatus({ kind: "error", message });
    }
  }

  return (
    <header className="api-bar">
      <div className="api-bar__row">
        <label>
          <span>API base URL</span>
          <input
            type="text"
            value={config.baseUrl}
            onChange={(e) => onChange({ ...config, baseUrl: e.target.value })}
            placeholder="http://localhost:8000"
          />
        </label>
        <label>
          <span>X-API-Key (opcional)</span>
          <input
            type="password"
            value={config.apiKey}
            onChange={(e) => onChange({ ...config, apiKey: e.target.value })}
            placeholder="(vacío si la API está abierta)"
          />
        </label>
        <button type="button" onClick={ping}>
          Probar conexión
        </button>
        {status.message && (
          <span className={`status status--${status.kind}`}>{status.message}</span>
        )}
      </div>
    </header>
  );
}
