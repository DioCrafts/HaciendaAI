import type {
  ApiError,
  DeductionSummary,
  RuleEvaluation,
  SimulationReport,
  TaxProfile,
} from "./types";

export interface ApiConfig {
  baseUrl: string;
  apiKey: string;
}

function buildHeaders(config: ApiConfig): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (config.apiKey.trim()) {
    headers["X-API-Key"] = config.apiKey.trim();
  }
  return headers;
}

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        detail = parsed.detail;
      }
    } catch {
      /* texto plano */
    }
    const error: ApiError = {
      message: detail || response.statusText,
      status: response.status,
    };
    throw error;
  }
  return (await response.json()) as T;
}

export async function fetchHealth(config: ApiConfig): Promise<{ status: string; version: string }> {
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}/health`);
  return unwrap(response);
}

export async function fetchDeductions(
  config: ApiConfig,
  filters: { region?: string; taxYear?: number } = {},
): Promise<DeductionSummary[]> {
  const params = new URLSearchParams();
  if (filters.region) params.set("region", filters.region);
  if (filters.taxYear) params.set("tax_year", String(filters.taxYear));
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}/v1/deductions${query}`, {
    headers: buildHeaders(config),
  });
  return unwrap(response);
}

export async function evaluateProfile(
  config: ApiConfig,
  profile: TaxProfile,
): Promise<RuleEvaluation[]> {
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}/v1/evaluate`, {
    method: "POST",
    headers: buildHeaders(config),
    body: JSON.stringify(profile),
  });
  return unwrap(response);
}

export async function simulateProfile(
  config: ApiConfig,
  profile: TaxProfile,
): Promise<SimulationReport> {
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}/v1/simulate`, {
    method: "POST",
    headers: buildHeaders(config),
    body: JSON.stringify(profile),
  });
  return unwrap(response);
}
