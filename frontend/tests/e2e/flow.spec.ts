import { expect, test } from "@playwright/test";

test.describe("Flujo perfil → evaluate → simulate", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("la página carga con el formulario de perfil visible", async ({ page }) => {
    await expect(page).toHaveTitle(/HaciendaAI/);
    await expect(page.getByRole("heading", { name: "Perfil fiscal" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cargar perfil de ejemplo" })).toBeVisible();
    // Tabs presentes
    await expect(page.getByRole("button", { name: "Evaluación" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Simulación" })).toBeVisible();
  });

  test("ping /health responde OK", async ({ page }) => {
    await page.getByRole("button", { name: "Probar conexión" }).click();
    await expect(page.getByText(/OK\s*·\s*v\d/)).toBeVisible();
  });

  test("evaluación con perfil de ejemplo muestra grupos por estado", async ({ page }) => {
    await page.getByRole("button", { name: "Cargar perfil de ejemplo" }).click();
    // El select de CCAA debe ahora valer Madrid
    await expect(page.getByLabel("Comunidad autónoma")).toHaveValue("Madrid");

    await page.getByRole("button", { name: "Evaluar" }).click();

    // Resumen con el número total de deducciones evaluadas.
    await expect(page.getByText(/deducciones evaluadas/)).toBeVisible();

    // Las 11 reglas estatales + 1 autonómica de Madrid quedan en "Pendiente
    // de validación" (al estar todas pendiente_tests / pendiente_fuente).
    // Las otras 14 autonómicas filtran a "No aplica" por región.
    await expect(page.getByRole("heading", { name: /Pendiente de validación/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /No aplica/ })).toBeVisible();

    // Comprobamos que aparece al menos un ID conocido del corpus.
    await expect(page.getByText("es_donativos_no_recurrente_2025").first()).toBeVisible();
    await expect(page.getByText("auto_madrid_alquiler_jovenes_2025").first()).toBeVisible();
  });

  test("simulación muestra los dos modos de tributación con badge recomendada", async ({ page }) => {
    await page.getByRole("button", { name: "Cargar perfil de ejemplo" }).click();
    await page.getByRole("button", { name: "Simulación" }).click();
    await page.getByRole("button", { name: "Simular" }).click();

    await expect(page.getByRole("heading", { name: /Tributación individual/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Tributación conjunta/ })).toBeVisible();

    // Las tres tarjetas de escenario aparecen para cada modo (6 en total).
    await expect(page.getByRole("heading", { name: "conservador" })).toHaveCount(2);
    await expect(page.getByRole("heading", { name: "esperado" })).toHaveCount(2);
    await expect(page.getByRole("heading", { name: "optimizado" })).toHaveCount(2);

    // Una de las dos columnas lleva el badge "recomendada".
    await expect(page.getByText("recomendada")).toHaveCount(1);
  });

  test("evaluación sin región muestra error 400 del backend", async ({ page }) => {
    // No cargamos el ejemplo: la región por defecto es cadena vacía y el
    // backend rechaza con HTTP 400 indicando que region es obligatoria.
    await page.getByRole("button", { name: "Evaluar" }).click();
    await expect(page.getByText(/Error:/)).toBeVisible();
    await expect(page.getByText(/region/i)).toBeVisible();
  });
});
