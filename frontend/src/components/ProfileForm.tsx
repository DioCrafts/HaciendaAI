import { useMemo, useState } from "react";
import type { TaxProfile } from "../types";

const REGIONS_REGIMEN_COMUN = [
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
];

interface Props {
  profile: TaxProfile;
  onChange: (profile: TaxProfile) => void;
  onLoadExample: () => void;
}

interface NestedNumberFieldProps {
  label: string;
  group: "personal" | "income" | "family" | "expenses" | "taxable_base";
  field: string;
  profile: TaxProfile;
  onChange: (profile: TaxProfile) => void;
  step?: number;
}

function NestedNumberField({
  label,
  group,
  field,
  profile,
  onChange,
  step = 1,
}: NestedNumberFieldProps): React.JSX.Element {
  const groupValue = (profile[group] as Record<string, unknown> | undefined) ?? {};
  const value = groupValue[field];
  const numericValue = typeof value === "number" ? value : "";
  return (
    <label>
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={numericValue}
        onChange={(e) => {
          const raw = e.target.value;
          const next = { ...groupValue };
          if (raw === "") {
            delete next[field];
          } else {
            next[field] = Number(raw);
          }
          onChange({ ...profile, [group]: next });
        }}
      />
    </label>
  );
}

interface NestedBooleanFieldProps {
  label: string;
  group: "personal" | "family";
  field: string;
  profile: TaxProfile;
  onChange: (profile: TaxProfile) => void;
}

function NestedBooleanField({
  label,
  group,
  field,
  profile,
  onChange,
}: NestedBooleanFieldProps): React.JSX.Element {
  const groupValue = (profile[group] as Record<string, unknown> | undefined) ?? {};
  const value = Boolean(groupValue[field]);
  return (
    <label className="checkbox">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => {
          const next = { ...groupValue, [field]: e.target.checked };
          onChange({ ...profile, [group]: next });
        }}
      />
      <span>{label}</span>
    </label>
  );
}

export function ProfileForm({ profile, onChange, onLoadExample }: Props): React.JSX.Element {
  const personal = (profile.personal as Record<string, unknown> | undefined) ?? {};
  const family = (profile.family as Record<string, unknown> | undefined) ?? {};
  const spouse = (family["spouse"] as Record<string, unknown> | undefined) ?? {};

  const [documentsText, setDocumentsText] = useState<string>(
    (profile.documents ?? []).join("\n"),
  );
  const documents = useMemo(
    () =>
      documentsText
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0),
    [documentsText],
  );

  function commitDocuments(text: string): void {
    setDocumentsText(text);
    const parsed = text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    onChange({ ...profile, documents: parsed });
  }

  return (
    <section className="profile-form">
      <div className="profile-form__header">
        <h2>Perfil fiscal</h2>
        <button type="button" className="ghost" onClick={onLoadExample}>
          Cargar perfil de ejemplo
        </button>
      </div>

      <fieldset>
        <legend>Identificación fiscal</legend>
        <div className="grid">
          <label>
            <span>Ejercicio fiscal</span>
            <input
              type="number"
              value={profile.tax_year}
              onChange={(e) => onChange({ ...profile, tax_year: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Comunidad autónoma</span>
            <select
              value={profile.region}
              onChange={(e) => onChange({ ...profile, region: e.target.value })}
            >
              <option value="">— elige CCAA —</option>
              {REGIONS_REGIMEN_COMUN.map((region) => (
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
              onChange={(e) =>
                onChange({
                  ...profile,
                  filing_mode: e.target.value as TaxProfile["filing_mode"],
                })
              }
            >
              <option value="unknown">Sin especificar</option>
              <option value="individual">Individual</option>
              <option value="conjunta">Conjunta</option>
            </select>
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Datos personales y familia</legend>
        <div className="grid">
          <NestedNumberField
            label="Edad"
            group="personal"
            field="age"
            profile={profile}
            onChange={onChange}
          />
          <label>
            <span>Categoría de familia numerosa</span>
            <select
              value={(personal["large_family_category"] as string | undefined) ?? ""}
              onChange={(e) => {
                const value = e.target.value;
                const next = { ...personal };
                if (value === "") {
                  delete next["large_family_category"];
                } else {
                  next["large_family_category"] = value;
                }
                onChange({ ...profile, personal: next });
              }}
            >
              <option value="">No aplica</option>
              <option value="general">General</option>
              <option value="especial">Especial</option>
            </select>
          </label>
        </div>
        <div className="grid">
          <NestedBooleanField
            label="Elegible para deducción por maternidad"
            group="personal"
            field="is_eligible_maternity_deduction"
            profile={profile}
            onChange={onChange}
          />
          <NestedBooleanField
            label="Colegiación obligatoria para mi actividad"
            group="personal"
            field="professional_association_required"
            profile={profile}
            onChange={onChange}
          />
          <NestedBooleanField
            label="Donativo recurrente (≥2 años a la misma entidad)"
            group="personal"
            field="donations_recurrent_qualifying"
            profile={profile}
            onChange={onChange}
          />
        </div>
        <div className="grid">
          <NestedNumberField
            label="Meses cualificantes maternidad (suma por hijo)"
            group="family"
            field="maternity_qualifying_child_months"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Meses con título de familia numerosa"
            group="family"
            field="large_family_qualifying_months"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Meses descendiente con discapacidad (suma por descendiente)"
            group="family"
            field="disabled_descendants_qualifying_months"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Meses ascendiente con discapacidad (suma por ascendiente)"
            group="family"
            field="disabled_ascendants_qualifying_months"
            profile={profile}
            onChange={onChange}
          />
          <label>
            <span>Renta del cónyuge (€)</span>
            <input
              type="number"
              value={typeof spouse["work_income"] === "number" ? (spouse["work_income"] as number) : ""}
              onChange={(e) => {
                const raw = e.target.value;
                const nextSpouse = { ...spouse };
                if (raw === "") {
                  delete nextSpouse["work_income"];
                } else {
                  nextSpouse["work_income"] = Number(raw);
                }
                const nextFamily = { ...family, spouse: nextSpouse };
                onChange({ ...profile, family: nextFamily });
              }}
            />
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Ingresos y bases</legend>
        <div className="grid">
          <NestedNumberField
            label="Rendimientos del trabajo (€)"
            group="income"
            field="work_income"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Base liquidable (€)"
            group="taxable_base"
            field="liquidable"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Base general (€)"
            group="taxable_base"
            field="general"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Base del ahorro (€)"
            group="taxable_base"
            field="savings"
            profile={profile}
            onChange={onChange}
          />
        </div>
      </fieldset>

      <fieldset>
        <legend>Gastos deducibles y donativos</legend>
        <div className="grid">
          <NestedNumberField
            label="Cuotas sindicales (€)"
            group="expenses"
            field="union_dues_amount"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Cuotas colegios profesionales (€)"
            group="expenses"
            field="professional_association_fees_amount"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Aportaciones plan de pensiones (€)"
            group="expenses"
            field="pension_plan_contribution_amount"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Aportaciones plan pensiones cónyuge (€)"
            group="expenses"
            field="spouse_pension_plan_contribution_amount"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Donativos a entidades Ley 49/2002 (€)"
            group="expenses"
            field="donations_amount"
            profile={profile}
            onChange={onChange}
          />
          <NestedNumberField
            label="Alquiler vivienda habitual (€/año)"
            group="expenses"
            field="rent_amount"
            profile={profile}
            onChange={onChange}
          />
        </div>
      </fieldset>

      <fieldset>
        <legend>Documentos disponibles ({documents.length})</legend>
        <p className="muted">
          Uno por línea. Las reglas comparan literalmente la cadena, así que conviene copiar el
          texto de la columna &ldquo;Documento requerido&rdquo; tal cual aparece en el corpus.
        </p>
        <textarea
          rows={8}
          value={documentsText}
          onChange={(e) => commitDocuments(e.target.value)}
        />
      </fieldset>
    </section>
  );
}
