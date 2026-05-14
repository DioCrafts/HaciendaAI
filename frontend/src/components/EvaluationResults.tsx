import type { RuleEvaluation, RuleStatus } from "../types";

const STATUS_ORDER: RuleStatus[] = [
  "applies",
  "missing_evidence",
  "missing_data",
  "pending_validation",
  "does_not_apply",
];

const STATUS_LABELS: Record<RuleStatus, string> = {
  applies: "Aplica",
  missing_evidence: "Falta documentación",
  missing_data: "Faltan datos",
  pending_validation: "Pendiente de validación",
  does_not_apply: "No aplica",
};

function formatEuros(amount: number): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
  }).format(amount);
}

interface Props {
  evaluations: RuleEvaluation[] | null;
  loading: boolean;
  error: string | null;
}

export function EvaluationResults({ evaluations, loading, error }: Props): React.JSX.Element {
  if (loading) {
    return <p className="muted">Evaluando…</p>;
  }
  if (error) {
    return <p className="error">Error: {error}</p>;
  }
  if (!evaluations) {
    return (
      <p className="muted">
        Pulsa <strong>Evaluar</strong> para enviar el perfil al motor.
      </p>
    );
  }

  const grouped = new Map<RuleStatus, RuleEvaluation[]>();
  for (const evaluation of evaluations) {
    const list = grouped.get(evaluation.status) ?? [];
    list.push(evaluation);
    grouped.set(evaluation.status, list);
  }

  const total = evaluations
    .filter((e) => e.status === "applies" || e.status === "missing_evidence")
    .reduce((sum, e) => sum + e.estimated_amount, 0);

  return (
    <section className="results">
      <div className="results__summary">
        <p>
          <strong>{evaluations.length}</strong> deducciones evaluadas. Importe estimado (
          <em>applies + missing_evidence</em>): <strong>{formatEuros(total)}</strong>.
        </p>
      </div>
      {STATUS_ORDER.map((status) => {
        const items = grouped.get(status);
        if (!items || items.length === 0) {
          return null;
        }
        return (
          <div key={status} className={`status-group status-group--${status}`}>
            <h3>
              {STATUS_LABELS[status]} <span className="count">({items.length})</span>
            </h3>
            <ul>
              {items.map((evaluation) => (
                <li key={evaluation.deduction_id}>
                  <div className="rule-row">
                    <span className="rule-row__id">{evaluation.deduction_id}</span>
                    <span className="rule-row__amount">
                      {formatEuros(evaluation.estimated_amount)}
                    </span>
                  </div>
                  <p className="rule-row__reason">{evaluation.reason}</p>
                  {evaluation.missing_fields.length > 0 && (
                    <p className="rule-row__missing">
                      Campos faltantes: {evaluation.missing_fields.join(", ")}
                    </p>
                  )}
                  {evaluation.missing_documents.length > 0 && (
                    <p className="rule-row__missing">
                      Documentos faltantes: {evaluation.missing_documents.join(", ")}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
