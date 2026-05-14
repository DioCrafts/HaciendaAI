import type { TaxProfile } from "./types";

export const exampleProfile: TaxProfile = {
  tax_year: 2025,
  region: "Madrid",
  filing_mode: "individual",
  personal: {
    age: 31,
    is_eligible_maternity_deduction: true,
    professional_association_required: true,
    donations_recurrent_qualifying: true,
    large_family_category: "general",
  },
  income: {
    work_income: 32000,
  },
  family: {
    spouse: { work_income: 5000 },
    maternity_qualifying_child_months: 12,
    large_family_qualifying_months: 12,
    disabled_descendants_qualifying_months: 0,
    disabled_ascendants_qualifying_months: 0,
  },
  expenses: {
    union_dues_amount: 220,
    professional_association_fees_amount: 380,
    pension_plan_contribution_amount: 1800,
    spouse_pension_plan_contribution_amount: 800,
    donations_amount: 350,
    rent_amount: 9000,
  },
  taxable_base: {
    liquidable: 28000,
    general: 30000,
  },
  documents: [
    "Justificante de pago de cuotas sindicales",
    "Justificante de cuotas colegiales",
    "Certificado de aportación al plan de pensiones",
    "Certificado de aportación al plan de pensiones del cónyuge",
    "Certificado de donativo expedido por la entidad beneficiaria",
    "Justificación de donativos a la misma entidad en los dos ejercicios anteriores por importe igual o superior",
    "Libro de familia o certificación equivalente",
    "Vida laboral o documento que acredite la situación laboral",
    "Título de familia numerosa en vigor",
    "Contrato de arrendamiento y justificantes de pago",
  ],
};
