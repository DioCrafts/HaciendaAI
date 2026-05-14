# HaciendaAI Frontend

Frontend mínimo en React + TypeScript + Vite que consume la API HTTP del backend (`POST /v1/evaluate`, `POST /v1/simulate`, `GET /v1/deductions`).

## Requisitos

- Node 20 o superior.
- Backend corriendo (por defecto, `http://localhost:8000`):

```bash
# desde la raíz del repo
pip install -e ".[api]"
hacienda-ai serve --port 8000
```

## Comandos

```bash
cd frontend
npm install         # primera vez
npm run dev         # servidor de desarrollo en http://localhost:5173 (hot reload)
npm run typecheck   # tsc --noEmit
npm run build       # genera dist/ optimizado
npm run preview     # sirve dist/ en http://localhost:4173
```

## Uso

1. Arranca el backend (`hacienda-ai serve`) y el frontend (`npm run dev`).
2. Abre <http://localhost:5173>.
3. En la barra superior, ajusta la URL base del API si no es la por defecto y rellena la `X-API-Key` si la activaste con `hacienda-ai serve --api-key ...`.
4. Pulsa **Probar conexión** para verificar el `/health`.
5. Rellena el formulario del perfil (o pulsa **Cargar perfil de ejemplo** para tener un perfil completo de prueba).
6. Cambia entre las pestañas **Evaluación** y **Simulación** y pulsa el botón principal.

## Estructura

```
src/
├── api.ts                     # fetch wrappers tipados a la API
├── types.ts                   # tipos TS espejo de los dataclasses Python
├── exampleProfile.ts          # perfil de prueba con campos de los 3 lotes
├── App.tsx                    # composición de tabs y estado
├── main.tsx                   # bootstrap React 18 + StrictMode
├── components/
│   ├── ApiConfigBar.tsx       # base URL + X-API-Key + ping a /health
│   ├── ProfileForm.tsx        # formulario estructurado del TaxProfile
│   ├── EvaluationResults.tsx  # lista agrupada por estado con totales
│   └── SimulationView.tsx     # 3 escenarios × 2 modos + recomendado
└── styles/
    └── index.css              # CSS plano con tema claro/oscuro automático
```

## Limitaciones conocidas

- El formulario cubre los campos consumidos por las 11 reglas estatales y la deducción autonómica de alquiler joven (los del corpus actual). Para campos no contemplados, el usuario puede editar el JSON exportado y enviarlo manualmente al API.
- No hay tests automatizados del frontend; la verificación es visual contra el backend en local.
- No hay almacenamiento local del perfil — al refrescar se pierde.
- Sin internacionalización: textos en español.
- La lista de "documentos" se rellena como texto libre (uno por línea); las reglas comparan literalmente. El ejemplo precarga los strings exactos del corpus.
