// Tipos TypeScript que reflejan los dataclasses de
// `src/hacienda_ai/models.py`. Mantener en sincronía manual.

export type FilingMode = "individual" | "conjunta" | "unknown";

export type LargeFamilyCategory = "general" | "especial";

export type RuleStatus =
  | "applies"
  | "does_not_apply"
  | "missing_data"
  | "missing_evidence"
  | "pending_validation";

export type ScenarioName = "conservador" | "esperado" | "optimizado";

export interface TaxProfile {
  tax_year: number;
  region: string;
  filing_mode?: FilingMode;
  personal?: Record<string, unknown>;
  family?: Record<string, unknown>;
  income?: Record<string, unknown>;
  withholdings?: unknown[];
  expenses?: Record<string, unknown>;
  taxable_base?: Record<string, unknown>;
  documents?: string[];
}

export interface Source {
  type: string;
  title: string;
  url: string | null;
  checked_at: string | null;
}

export interface RuleEvaluation {
  deduction_id: string;
  status: RuleStatus;
  estimated_amount: number;
  reason: string;
  missing_fields: string[];
  missing_documents: string[];
  sources: Source[];
  risk_level: "low" | "medium" | "high";
  confidence: number;
}

export interface DeductionSummary {
  id: string;
  name: string;
  scope: "estatal" | "autonomico" | "local";
  region: string | null;
  category: string;
  tax_year: number;
  validation_status: string;
  risk_level: string;
}

export interface Scenario {
  name: ScenarioName;
  description: string;
  filing_mode: "individual" | "conjunta";
  total_estimated_amount: number;
  included_deduction_ids: string[];
}

export interface FilingScenarios {
  filing_mode: "individual" | "conjunta";
  scenarios: Scenario[];
}

export interface SimulationReport {
  tax_year: number;
  region: string;
  requested_filing_mode: string;
  individual: FilingScenarios;
  conjunta: FilingScenarios;
  recommended_filing_mode: "individual" | "conjunta";
}

export interface ApiError {
  message: string;
  status: number;
}
