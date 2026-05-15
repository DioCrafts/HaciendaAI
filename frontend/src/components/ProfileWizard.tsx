import { useState } from "react";
import type { TaxProfile } from "../types";

const REGIONS = [
  "Andalucía",
  "Aragón",
  "Asturias",
  "Illes Balears",
  "Canarias",
  "Cantabria",
  "Castilla-La Mancha",
  "Castilla y León",
  "Cataluña",
  "Comunitat Valenciana",
  "Extremadura",
  "Galicia",
  "La Rioja",
  "Madrid",
  "Murcia",
  "Ceuta",
  "Melilla",
];

type StepId = "identification" | "personal" | "family" | "income" | "expenses" | "summary";

interface StepDescriptor {
  id: StepId;
  title: string;
}

const STEPS: StepDescriptor[] = [
  { id: "identification", title: "Identificación fiscal" },
  { id: "personal", title: "Datos personales" },
  { id: "family", title: "Composición familiar" },
  { id: "income", title: "Ingresos y bases" },
  { id: "expenses", title: "Gastos deducibles y donativos" },
  { id: "summary", title: "Resumen" },
];

interface Props {
  profile: TaxProfile;
  onChange: (profile: TaxProfile) => void;
  onClear: () => void;
}

function updateGroup<K extends "personal" | "family" | "income" | "expenses" | "taxable_base">(
  profile: TaxProfile,
  group: K,
  key: string,
  value: unknown,
): TaxProfile {
  const current = (profile[group] as Record<string, unknown> | undefined) ?? {};
  const next = { ...current };
  if (value === "" || value === undefined || value === null || (typeof value === "number" && Number.isNaN(value))) {
    delete next[key];
  } else {
    next[key] = value;
  }
  return { ...profile, [group]: next };
}

export function ProfileWizard({ profile, onChange, onClear }: Props): React.JSX.Element {
  const [stepIndex, setStepIndex] = useState<number>(0);
  const step = STEPS[stepIndex]!;

  const personal = (profile.personal as Record<string, unknown> | undefined) ?? {};
  const family = (profile.family as Record<string, unknown> | undefined) ?? {};
  const income = (profile.income as Record<string, unknown> | undefined) ?? {};
  const expenses = (profile.expenses as Record<string, unknown> | undefined) ?? {};
  const taxable_base = (profile.taxable_base as Record<string, unknown> | undefined) ?? {};

  function setNumber(group: "personal" | "family" | "income" | "expenses" | "taxable_base", key: string, raw: string): void {
    onChange(updateGroup(profile, group, key, raw === "" ? "" : Number(raw)));
  }

  function setBoolean(group: "personal" | "family", key: string, value: boolean): void {
    onChange(updateGroup(profile, group, key, value));
  }

  function setString(group: "personal" | "family", key: string, value: string): void {
    onChange(updateGroup(profile, group, key, value));
  }

  return (
    <section className="wizard">
      <header className="wizard__header">
        <div>
          <h2>Wizard guiado</h2>
          <p className="muted">
            Paso {stepIndex + 1} de {STEPS.length}: <strong>{step.title}</strong>
          </p>
        </div>
        <button type="button" className="ghost" onClick={onClear}>
          Empezar de nuevo
        </button>
      </header>

      <ol className="wizard__progress">
        {STEPS.map((descriptor, index) => (
          <li
            key={descriptor.id}
            className={`wizard__progress-item ${index === stepIndex ? "active" : ""} ${index < stepIndex ? "done" : ""}`}
          >
            <button
              type="button"
              onClick={() => setStepIndex(index)}
              className="wizard__progress-button"
              aria-current={index === stepIndex ? "step" : undefined}
            >
              {index + 1}. {descriptor.title}
            </button>
          </li>
        ))}
      </ol>

      <div className="wizard__step">
        {step.id === "identification" && (
          <fieldset>
            <legend>Identificación fiscal</legend>
            <div className="grid">
              <label>
                <span>Ejercicio fiscal</span>
                <input
                  type="number"
                  value={profile.tax_year}
                  onChange={(event) => onChange({ ...profile, tax_year: Number(event.target.value) })}
                />
              </label>
              <label>
                <span>Comunidad autónoma</span>
                <select
                  value={profile.region}
                  onChange={(event) => onChange({ ...profile, region: event.target.value })}
                >
                  <option value="">— elige CCAA —</option>
                  {REGIONS.map((region) => (
                    <option key={region} value={region}>
                      {region}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Modo de tributación</span>
                <select
                  value={profile.filing_mode ?? "unknown"}
                  onChange={(event) =>
                    onChange({ ...profile, filing_mode: event.target.value as TaxProfile["filing_mode"] })
                  }
                >
                  <option value="unknown">Sin especificar</option>
                  <option value="individual">Individual</option>
                  <option value="conjunta">Conjunta</option>
                </select>
              </label>
            </div>
          </fieldset>
        )}

        {step.id === "personal" && (
          <fieldset>
            <legend>Datos personales</legend>
            <div className="grid">
              <label>
                <span>Edad</span>
                <input
                  type="number"
                  value={typeof personal["age"] === "number" ? (personal["age"] as number) : ""}
                  onChange={(event) => setNumber("personal", "age", event.target.value)}
                />
              </label>
              <label>
                <span>Grado de discapacidad (%)</span>
                <input
                  type="number"
                  value={
                    typeof personal["disability_percentage"] === "number"
                      ? (personal["disability_percentage"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("personal", "disability_percentage", event.target.value)}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(personal["needs_third_person_help"])}
                  onChange={(event) => setBoolean("personal", "needs_third_person_help", event.target.checked)}
                />
                <span>Necesito ayuda de tercera persona o tengo movilidad reducida</span>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(personal["professional_association_required"])}
                  onChange={(event) =>
                    setBoolean("personal", "professional_association_required", event.target.checked)
                  }
                />
                <span>Colegiación obligatoria para mi actividad</span>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(personal["is_eligible_maternity_deduction"])}
                  onChange={(event) =>
                    setBoolean("personal", "is_eligible_maternity_deduction", event.target.checked)
                  }
                />
                <span>Soy mujer trabajadora elegible para la deducción por maternidad</span>
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(personal["donations_recurrent_qualifying"])}
                  onChange={(event) =>
                    setBoolean("personal", "donations_recurrent_qualifying", event.target.checked)
                  }
                />
                <span>He donado a la misma entidad cantidades ≥ en los dos años anteriores</span>
              </label>
              <label>
                <span>Categoría de familia numerosa</span>
                <select
                  value={(personal["large_family_category"] as string | undefined) ?? ""}
                  onChange={(event) => setString("personal", "large_family_category", event.target.value)}
                >
                  <option value="">No aplica</option>
                  <option value="general">General</option>
                  <option value="especial">Especial</option>
                </select>
              </label>
            </div>
          </fieldset>
        )}

        {step.id === "family" && (
          <fieldset>
            <legend>Composición familiar</legend>
            <div className="grid">
              <label>
                <span>Hijos (total)</span>
                <input
                  type="number"
                  value={typeof family["children_count"] === "number" ? (family["children_count"] as number) : ""}
                  onChange={(event) => setNumber("family", "children_count", event.target.value)}
                />
              </label>
              <label>
                <span>Hijos &lt; 3 años</span>
                <input
                  type="number"
                  value={
                    typeof family["children_under_3_count"] === "number"
                      ? (family["children_under_3_count"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "children_under_3_count", event.target.value)}
                />
              </label>
              <label>
                <span>Meses cualificantes maternidad (suma por hijo)</span>
                <input
                  type="number"
                  value={
                    typeof family["maternity_qualifying_child_months"] === "number"
                      ? (family["maternity_qualifying_child_months"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "maternity_qualifying_child_months", event.target.value)}
                />
              </label>
              <label>
                <span>Meses con título familia numerosa</span>
                <input
                  type="number"
                  value={
                    typeof family["large_family_qualifying_months"] === "number"
                      ? (family["large_family_qualifying_months"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "large_family_qualifying_months", event.target.value)}
                />
              </label>
              <label>
                <span>Ascendientes cualificantes (≥ 65 o discapacidad)</span>
                <input
                  type="number"
                  value={
                    typeof family["ascendants_qualifying_count"] === "number"
                      ? (family["ascendants_qualifying_count"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "ascendants_qualifying_count", event.target.value)}
                />
              </label>
              <label>
                <span>Descendientes con discapacidad ≥ 33 % y &lt; 65 %</span>
                <input
                  type="number"
                  value={
                    typeof family["disabled_descendants_33_64_count"] === "number"
                      ? (family["disabled_descendants_33_64_count"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "disabled_descendants_33_64_count", event.target.value)}
                />
              </label>
              <label>
                <span>Descendientes con discapacidad ≥ 65 %</span>
                <input
                  type="number"
                  value={
                    typeof family["disabled_descendants_65_plus_count"] === "number"
                      ? (family["disabled_descendants_65_plus_count"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("family", "disabled_descendants_65_plus_count", event.target.value)}
                />
              </label>
            </div>
          </fieldset>
        )}

        {step.id === "income" && (
          <fieldset>
            <legend>Ingresos y bases</legend>
            <div className="grid">
              <label>
                <span>Rendimientos íntegros del trabajo (€)</span>
                <input
                  type="number"
                  value={typeof income["work_income"] === "number" ? (income["work_income"] as number) : ""}
                  onChange={(event) => setNumber("income", "work_income", event.target.value)}
                />
              </label>
              <label>
                <span>Base imponible general (€)</span>
                <input
                  type="number"
                  value={
                    typeof taxable_base["general"] === "number" ? (taxable_base["general"] as number) : ""
                  }
                  onChange={(event) => setNumber("taxable_base", "general", event.target.value)}
                />
              </label>
              <label>
                <span>Base imponible del ahorro (€)</span>
                <input
                  type="number"
                  value={
                    typeof taxable_base["savings"] === "number" ? (taxable_base["savings"] as number) : ""
                  }
                  onChange={(event) => setNumber("taxable_base", "savings", event.target.value)}
                />
              </label>
              <label>
                <span>Base liquidable (€) — para tope de donativos</span>
                <input
                  type="number"
                  value={
                    typeof taxable_base["liquidable"] === "number" ? (taxable_base["liquidable"] as number) : ""
                  }
                  onChange={(event) => setNumber("taxable_base", "liquidable", event.target.value)}
                />
              </label>
              <label>
                <span>Rendimientos netos del trabajo + actividades (€)</span>
                <input
                  type="number"
                  value={
                    typeof taxable_base["net_work_and_economic_income"] === "number"
                      ? (taxable_base["net_work_and_economic_income"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("taxable_base", "net_work_and_economic_income", event.target.value)}
                />
              </label>
            </div>
          </fieldset>
        )}

        {step.id === "expenses" && (
          <fieldset>
            <legend>Gastos deducibles y donativos</legend>
            <div className="grid">
              <label>
                <span>Cuotas sindicales (€)</span>
                <input
                  type="number"
                  value={
                    typeof expenses["union_dues_amount"] === "number"
                      ? (expenses["union_dues_amount"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("expenses", "union_dues_amount", event.target.value)}
                />
              </label>
              <label>
                <span>Cuotas colegios profesionales (€)</span>
                <input
                  type="number"
                  value={
                    typeof expenses["professional_association_fees_amount"] === "number"
                      ? (expenses["professional_association_fees_amount"] as number)
                      : ""
                  }
                  onChange={(event) =>
                    setNumber("expenses", "professional_association_fees_amount", event.target.value)
                  }
                />
              </label>
              <label>
                <span>Aportaciones plan de pensiones (€)</span>
                <input
                  type="number"
                  value={
                    typeof expenses["pension_plan_contribution_amount"] === "number"
                      ? (expenses["pension_plan_contribution_amount"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("expenses", "pension_plan_contribution_amount", event.target.value)}
                />
              </label>
              <label>
                <span>Donativos Ley 49/2002 (€)</span>
                <input
                  type="number"
                  value={
                    typeof expenses["donations_amount"] === "number"
                      ? (expenses["donations_amount"] as number)
                      : ""
                  }
                  onChange={(event) => setNumber("expenses", "donations_amount", event.target.value)}
                />
              </label>
              <label>
                <span>Alquiler vivienda habitual (€/año)</span>
                <input
                  type="number"
                  value={
                    typeof expenses["rent_amount"] === "number" ? (expenses["rent_amount"] as number) : ""
                  }
                  onChange={(event) => setNumber("expenses", "rent_amount", event.target.value)}
                />
              </label>
            </div>
          </fieldset>
        )}

        {step.id === "summary" && (
          <fieldset>
            <legend>Resumen del perfil</legend>
            <p className="muted">
              Datos persistidos en tu navegador (localStorage). Pulsa <strong>Evaluar</strong> o
              <strong> Simular</strong> en el panel de la derecha para enviar el perfil al motor.
            </p>
            <pre className="wizard__summary-json">{JSON.stringify(profile, null, 2)}</pre>
          </fieldset>
        )}
      </div>

      <nav className="wizard__nav">
        <button type="button" onClick={() => setStepIndex(Math.max(0, stepIndex - 1))} disabled={stepIndex === 0}>
          ← Anterior
        </button>
        <span className="muted">
          {stepIndex + 1} / {STEPS.length}
        </span>
        <button
          type="button"
          className="primary"
          onClick={() => setStepIndex(Math.min(STEPS.length - 1, stepIndex + 1))}
          disabled={stepIndex === STEPS.length - 1}
        >
          Siguiente →
        </button>
      </nav>
    </section>
  );
}
