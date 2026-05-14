import { useState } from "react";
import { ApiConfigBar } from "./components/ApiConfigBar";
import { EvaluationResults } from "./components/EvaluationResults";
import { ProfileForm } from "./components/ProfileForm";
import { SimulationView } from "./components/SimulationView";
import { evaluateProfile, simulateProfile } from "./api";
import type { ApiConfig } from "./api";
import type { RuleEvaluation, SimulationReport, TaxProfile } from "./types";
import { exampleProfile } from "./exampleProfile";

type Tab = "evaluate" | "simulate";

const EMPTY_PROFILE: TaxProfile = {
  tax_year: 2025,
  region: "",
  filing_mode: "unknown",
  documents: [],
};

export function App(): React.JSX.Element {
  const [config, setConfig] = useState<ApiConfig>({ baseUrl: "http://localhost:8000", apiKey: "" });
  const [profile, setProfile] = useState<TaxProfile>(EMPTY_PROFILE);
  const [tab, setTab] = useState<Tab>("evaluate");

  const [evaluations, setEvaluations] = useState<RuleEvaluation[] | null>(null);
  const [evaluating, setEvaluating] = useState<boolean>(false);
  const [evaluateError, setEvaluateError] = useState<string | null>(null);

  const [report, setReport] = useState<SimulationReport | null>(null);
  const [simulating, setSimulating] = useState<boolean>(false);
  const [simulateError, setSimulateError] = useState<string | null>(null);

  async function runEvaluate(): Promise<void> {
    setEvaluating(true);
    setEvaluateError(null);
    try {
      const result = await evaluateProfile(config, profile);
      setEvaluations(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : describeError(err);
      setEvaluateError(message);
      setEvaluations(null);
    } finally {
      setEvaluating(false);
    }
  }

  async function runSimulate(): Promise<void> {
    setSimulating(true);
    setSimulateError(null);
    try {
      const result = await simulateProfile(config, profile);
      setReport(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : describeError(err);
      setSimulateError(message);
      setReport(null);
    } finally {
      setSimulating(false);
    }
  }

  return (
    <div className="app">
      <ApiConfigBar config={config} onChange={setConfig} />
      <main className="app__main">
        <div className="app__profile">
          <ProfileForm
            profile={profile}
            onChange={setProfile}
            onLoadExample={() => setProfile(exampleProfile)}
          />
        </div>
        <div className="app__results">
          <nav className="tabs">
            <button
              type="button"
              className={tab === "evaluate" ? "active" : ""}
              onClick={() => setTab("evaluate")}
            >
              Evaluación
            </button>
            <button
              type="button"
              className={tab === "simulate" ? "active" : ""}
              onClick={() => setTab("simulate")}
            >
              Simulación
            </button>
            <div className="tabs__actions">
              {tab === "evaluate" ? (
                <button type="button" className="primary" onClick={runEvaluate} disabled={evaluating}>
                  Evaluar
                </button>
              ) : (
                <button type="button" className="primary" onClick={runSimulate} disabled={simulating}>
                  Simular
                </button>
              )}
            </div>
          </nav>
          {tab === "evaluate" ? (
            <EvaluationResults evaluations={evaluations} loading={evaluating} error={evaluateError} />
          ) : (
            <SimulationView report={report} loading={simulating} error={simulateError} />
          )}
        </div>
      </main>
      <footer className="app__footer">
        <p>
          HaciendaAI · No sustituye a un asesor fiscal. Las reglas marcadas como{" "}
          <em>Pendiente de validación</em> requieren revisión humana antes de recomendarse.
        </p>
      </footer>
    </div>
  );
}

function describeError(err: unknown): string {
  if (err && typeof err === "object" && "message" in err && "status" in err) {
    const e = err as { message: string; status: number };
    return `HTTP ${e.status}: ${e.message}`;
  }
  return "Error desconocido";
}
