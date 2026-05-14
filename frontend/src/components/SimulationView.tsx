import type { FilingScenarios, SimulationReport } from "../types";

function formatEuros(amount: number): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
  }).format(amount);
}

function FilingColumn({
  filing,
  isRecommended,
}: {
  filing: FilingScenarios;
  isRecommended: boolean;
}): React.JSX.Element {
  const label = filing.filing_mode === "individual" ? "Tributación individual" : "Tributación conjunta";
  return (
    <div className={`filing-col ${isRecommended ? "filing-col--recommended" : ""}`}>
      <h3>
        {label}
        {isRecommended && <span className="badge">recomendada</span>}
      </h3>
      {filing.scenarios.map((scenario) => (
        <article key={scenario.name} className="scenario-card">
          <header>
            <h4>{scenario.name}</h4>
            <strong>{formatEuros(scenario.total_estimated_amount)}</strong>
          </header>
          <p className="scenario-card__desc">{scenario.description}</p>
          {scenario.included_deduction_ids.length > 0 ? (
            <ul>
              {scenario.included_deduction_ids.map((id) => (
                <li key={id}>{id}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">Ninguna deducción del corpus aplica en este escenario.</p>
          )}
        </article>
      ))}
    </div>
  );
}

interface Props {
  report: SimulationReport | null;
  loading: boolean;
  error: string | null;
}

export function SimulationView({ report, loading, error }: Props): React.JSX.Element {
  if (loading) {
    return <p className="muted">Simulando…</p>;
  }
  if (error) {
    return <p className="error">Error: {error}</p>;
  }
  if (!report) {
    return (
      <p className="muted">
        Pulsa <strong>Simular</strong> para generar los escenarios conservador / esperado /
        optimizado para los dos modos de tributación.
      </p>
    );
  }
  return (
    <section className="simulation">
      <header className="simulation__header">
        <p>
          Ejercicio <strong>{report.tax_year}</strong>, <strong>{report.region}</strong>. Modo
          declarado: <code>{report.requested_filing_mode}</code>.
        </p>
        <p>
          Modo recomendado por importe estimado en el escenario <em>esperado</em>:{" "}
          <strong>{report.recommended_filing_mode}</strong>.
        </p>
      </header>
      <div className="filing-grid">
        <FilingColumn filing={report.individual} isRecommended={report.recommended_filing_mode === "individual"} />
        <FilingColumn filing={report.conjunta} isRecommended={report.recommended_filing_mode === "conjunta"} />
      </div>
    </section>
  );
}
