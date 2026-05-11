# Cómo ejecutar

## Requisitos

- Python 3.11 o superior.
- `pytest` para tests.

## Tests

```bash
python -m pytest
```

## Uso básico desde Python

```python
from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile
from hacienda_ai.rules import evaluate_deductions

profile = TaxProfile.from_dict({"tax_year": 2025, "region": "Madrid"})
results = evaluate_deductions(load_deductions(), profile)
```
