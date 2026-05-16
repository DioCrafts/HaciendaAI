# Cómo ejecutar

## Requisitos

- Python 3.11 o superior.
- `pytest` para tests; `fastapi`+`uvicorn` para la demo HTTP.

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

## Uso básico desde Python

```python
from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile
from hacienda_ai.rules import evaluate_deductions

profile = TaxProfile.from_dict({"tax_year": 2024, "region": "Madrid"})
results = evaluate_deductions(load_deductions(), profile)
```

## Demo HTTP

```bash
python -m pip install -e ".[api]"
python -m hacienda_ai.api --port 8000
```

Abre `http://127.0.0.1:8000/` en el navegador. Se renderiza un formulario
con un perfil sintético; al pulsar "Evaluar" la página llama a `POST
/profiles` y `POST /evaluations` y muestra una tabla con estado, importe
estimado, riesgo y enlaces pinpoint a BOE por cada deducción.

Flags útiles:

- `--host 0.0.0.0` — abrir a la red local (por defecto solo `127.0.0.1`).
- `--reload` — recarga el código al modificarlo (desarrollo).

## Verificar el corpus contra BOE

```bash
python scripts/verify_seed.py
```

Sale `0` si todos los hashes SHA-256 declarados coinciden con el texto
consolidado vigente en BOE, `1` si hay drift, `2` ante error de red.
