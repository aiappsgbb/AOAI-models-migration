# Actualización automática de precios — `update_prices.py`

## Resumen

`tools/update_prices.py` mantiene sincronizados los costes por token en
`config/settings.yaml → cost_rates` con los precios publicados por Microsoft y
los proveedores externos (Google, Mistral).

Funciona en dos fases:

1. **Azure Retail Prices API** — consulta pública (sin autenticación) que
   devuelve los meters de todos los modelos Azure OpenAI.
2. **Precios externos hardcodeados** — para modelos desplegados desde Azure
   Marketplace / AI Foundry que no aparecen en esa API (Gemini, Mistral, etc.).

---

## Arquitectura del pipeline

```
┌──────────────────────────┐
│  Azure Retail Prices API │
│  prices.azure.com        │
└──────────┬───────────────┘
           │  paginated JSON
           ▼
   ┌───────────────┐     ┌──────────────────────┐
   │ fetch_all_    │     │   _EXTERNAL_PRICES    │
   │ meters()      │     │   (Gemini, Mistral…)  │
   └───────┬───────┘     └──────────┬───────────┘
           │                        │
           ▼                        │
   ┌───────────────┐               │
   │ build_price_  │               │
   │ table()       │               │
   └───────┬───────┘               │
           │  {model: {tt: price}} │
           ▼                        ▼
   ┌─────────────────────────────────────┐
   │        Merge (API tiene prioridad)  │
   └───────────────┬─────────────────────┘
                   │
                   ▼
   ┌─────────────────────────────────────┐
   │  _update_cost_rates_in_yaml()       │
   │  (preserva comentarios y claves     │
   │   que la API no cubre, ej. reasoning│
   └───────────────┬─────────────────────┘
                   │
            ┌──────┴──────┐
            │             │
        --apply       (default)
            │             │
            ▼             ▼
     Escribe YAML    Muestra diff
```

---

## Paso a paso

### 1. Obtención de meters (`fetch_all_meters`)

Consulta la URL pública:

```
GET https://prices.azure.com/api/retail/prices
    ?$filter=contains(productName,'OpenAI')
            and isPrimaryMeterRegion eq true
            and (unitOfMeasure eq '1K' or unitOfMeasure eq '1M')
```

- No necesita autenticación ni suscripción Azure.
- Respuesta paginada — el script sigue `NextPageLink` hasta que es `null`.
- Soporta divisas distintas de USD vía `--currency EUR`.

### 2. Filtrado de meters

Se descartan meters irrelevantes mediante `_SKIP_PATTERNS`:

| Patrón            | Razón                                        |
|-------------------|----------------------------------------------|
| `batch`           | Pricing de uso batch (diferente de online)    |
| `ft`, `finetuned` | Fine-tuning hosting/training                 |
| `rft`, `grader`   | Reinforcement fine-tuning                    |
| `codex`           | Modelos Codex legacy                         |
| `transcri`        | Whisper / transcripción                      |
| `nano`            | Modelos Nano                                 |
| `pp`              | Provisioned/Purchased pricing                |
| `img`, `sora`     | Generación de imagen / vídeo                 |
| `embed`           | Embeddings                                   |
| `dev`             | Deep Research / Dev meters                   |

Además, se descartan meters **regionales** (sin marca `Gl`/`Dz`).

### 3. Clasificación de modelo (`_MODEL_RULES`)

Cada meter se compara contra una lista **ordenada** de regex (primera coincidencia gana).
El orden es relevante: las variantes más específicas van primero.

| Prioridad | Regex                       | Clave settings.yaml  |
|-----------|-----------------------------|----------------------|
| 1         | `rt 1.5` / `aud 1.5`       | `gpt_realtime_15`    |
| 2         | `rt txt` / `aud 0828`       | `gpt_realtime_1`     |
| 3         | `mn tts` / `mini.tts`       | `tts`                |
| 4         | `4.1 mini` / `41 mn`        | `gpt41_mini`         |
| 5         | `4.1`                       | `gpt4`               |
| 6         | `4o mini`                   | `gpt4o_mini`         |
| 7         | `4o`                        | `gpt4o`              |
| 8         | `5.4 mini`                  | `gpt54_mini`         |
| 9         | `5.4`                       | `gpt5`               |
| 10        | `5.1`                       | `gpt51`              |
| 11        | `5.2`                       | `gpt52`              |
| 12        | `o4 mini`                   | `o4_mini`            |
| 13        | `o3 mini`                   | `o3_mini`            |
| 14        | `oss.120b`                  | `phi4`               |

### 4. Clasificación del tipo de token (`_classify_token_type`)

Detecta las abreviaturas usadas por Azure en el campo `meterName`:

| Token en meter         | Tipo devuelto    |
|------------------------|------------------|
| `inp`, ` in `          | `input`          |
| `out`, `opt`           | `output`         |
| `cchd`, `cd`, `cached` | `cached_input`  |
| `aud` + `inp`          | `audio_input`    |
| `aud` + `out`          | `audio_output`   |
| `aud` + `cached`       | `audio_cached`   |

### 5. Detección de tier (`_meter_tier`)

| Marca en meter          | Tier devuelto |
|-------------------------|---------------|
| `Gl`, `glbl`, `global`  | `global`      |
| `Dz`, `DZone`, `dzone`  | `datazone`    |
| (nada)                   | `None` → descartado |

Comportamiento de selección:
- Se usa el tier preferido (por defecto `global`).
- Si un modelo solo tiene meters del otro tier, se usa como fallback.
- De múltiples meters del mismo tier, se toma el **precio mínimo**.

### 6. Normalización de unidades

| `unitOfMeasure` del API | Conversión           |
|------------------------|----------------------|
| `1K`                   | Se usa tal cual      |
| `1M`                   | `price / 1000`       |

Todos los precios en `settings.yaml` están en **USD por 1K tokens**.

### 7. Aliases (`_ALIASES`)

Modelos que comparten pricing con otro modelo:

```python
_ALIASES = {
    "gpt5_reasoning": "gpt51",   # mismo deployment (gpt-5.1)
}
```

Si `gpt5_reasoning` no tiene meters propios, hereda los precios de `gpt51`.

### 8. Precios externos (`_EXTERNAL_PRICES`)

Para modelos que no están en la Azure Retail Prices API:

| Modelo            | Input ($/1K) | Output ($/1K) | Cached ($/1K)  | Fuente                                          |
|-------------------|-------------|---------------|----------------|------------------------------------------------|
| `gemini3_flash`   | 0.00015     | 0.0006        | 0.0000375      | [ai.google.dev/pricing](https://ai.google.dev/pricing) |
| `mistral_large_3` | 0.002       | 0.006         | 0.001          | [docs.mistral.ai](https://docs.mistral.ai/models/mistral-large-3-25-12) |

Regla de merge: **los precios del API tienen prioridad**. Los externos solo se
añaden para modelos que no aparezcan en el resultado del API.

### 9. Escritura YAML (`_update_cost_rates_in_yaml`)

- Lee el YAML existente y compara valor a valor.
- Solo modifica modelos que **ya existen** en `cost_rates` (no añade modelos nuevos).
- **Preserva** claves que la API no devuelve (ej. `reasoning`).
- Preserva la estructura y secciones fuera de `cost_rates`.
- Mantiene el **orden original** de las claves.
- Orden determinista de tipos de token:
  `input → output → cached_input → reasoning → audio_input → audio_output → audio_cached`.

---

## Uso

### Previsualizar cambios (dry-run, por defecto)

```bash
python tools/update_prices.py
```

Muestra los cambios detectados sin modificar ningún fichero.

### Aplicar cambios

```bash
python tools/update_prices.py --apply
```

### Preferir tier DataZone

```bash
python tools/update_prices.py --tier datazone --apply
```

### Otra divisa

```bash
python tools/update_prices.py --currency EUR
```

### Modo verbose (depuración)

```bash
python tools/update_prices.py --verbose
```

Muestra todos los meters clasificados, los descartados y los no reconocidos.

### Omitir precios externos

```bash
python tools/update_prices.py --skip-external
```

Solo usa la Azure Retail Prices API (ignora Gemini, Mistral, etc.).

### Fichero settings alternativo

```bash
python tools/update_prices.py --settings path/to/other_settings.yaml --apply
```

---

## Flags CLI completos

| Flag               | Valor por defecto               | Descripción                                           |
|--------------------|---------------------------------|-------------------------------------------------------|
| `--apply`          | `false` (dry-run)               | Escribe los cambios en `settings.yaml`                |
| `--tier`           | `global`                        | Tier preferido: `global` o `datazone`                 |
| `--currency`       | `USD`                           | Código de divisa para la API                          |
| `--settings`       | `config/settings.yaml`          | Ruta al fichero YAML de configuración                 |
| `--verbose`, `-v`  | `false`                         | Muestra todos los meters y clasificaciones             |
| `--skip-external`  | `false`                         | Omite precios hardcodeados (Gemini, Mistral, …)       |

---

## Programación periódica

### Windows Task Scheduler

```powershell
schtasks /create /tn "UpdatePrices" `
    /tr "python tools/update_prices.py --apply" `
    /sc weekly /d MON /st 08:00
```

### cron (Linux / macOS)

```cron
0 8 * * 1 cd /path/to/model_migration_eval && python tools/update_prices.py --apply
```

### GitHub Actions

```yaml
name: Update model prices
on:
  schedule:
    - cron: '0 8 * * 1'       # Cada lunes a las 08:00 UTC
  workflow_dispatch:            # Ejecución manual

jobs:
  update-prices:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install requests pyyaml
      - run: python tools/update_prices.py --apply
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: 'chore: update model token prices'
```

---

## Cómo añadir un nuevo modelo

### Modelo Azure OpenAI (en la Retail Prices API)

1. Ejecutar en modo verbose para ver los meters disponibles:
   ```bash
   python tools/update_prices.py --verbose 2>&1 | grep -i "unclassified"
   ```
2. Identificar el patrón del `meterName` del nuevo modelo.
3. Añadir una tupla `(regex, "clave_settings")` en `_MODEL_RULES`
   (respetar el orden: más específico primero).
4. Añadir la clave en `config/settings.yaml → cost_rates` con valores
   iniciales (se sobreescribirán en la primera ejecución).
5. Añadir un test parametrizado en `TestModelRules` de
   `tests/test_update_prices.py`.

### Modelo externo (Gemini, Mistral, otro)

1. Buscar los precios oficiales del proveedor y anotar la URL fuente.
2. Añadir una entrada en `_EXTERNAL_PRICES`:
   ```python
   "nueva_clave": {
       "source": "https://proveedor.com/pricing",
       "note": "Modelo X — Azure Marketplace pay-as-you-go",
       "rates": {
           "input": 0.001,          # USD per 1K tokens
           "output": 0.003,
           "cached_input": 0.0005,
       },
   },
   ```
3. Añadir la clave en `config/settings.yaml → cost_rates`.
4. Ejecutar en modo dry-run para verificar el merge.
5. Añadir tests en `TestExternalPrices`.

---

## Estructura de `cost_rates` en settings.yaml

```yaml
cost_rates:
  <model_key>:
    input: <float>           # USD por 1K tokens de entrada (texto)
    output: <float>          # USD por 1K tokens de salida
    cached_input: <float>    # USD por 1K tokens de caché de entrada
    reasoning: <float>       # (opcional) tokens de razonamiento
    audio_input: <float>     # (opcional) tokens de audio entrada
    audio_output: <float>    # (opcional) tokens de audio salida
    audio_cached: <float>    # (opcional) tokens de audio caché
```

Modelos sin clave propia usan los valores de `default`.

---

## Tests

Los tests están en `tests/test_update_prices.py` — **60 tests** organizados en:

| Clase                      | Tests | Qué cubre                                               |
|----------------------------|------:|----------------------------------------------------------|
| `TestClassifyTokenType`    |     8 | Clasificación de tipo de token (input, output, cached…)  |
| `TestMeterTier`            |     5 | Detección de tier (global, datazone, regional)           |
| `TestModelRules`           |    17 | Mapeo meter → model_key (parametrizado)                  |
| `TestBuildPriceTable`      |     9 | Pipeline completo: normalización, fallback, aliases       |
| `TestUpdateCostRatesInYaml`|     6 | Merge YAML: preservación de claves, nuevas, sin cambios  |
| `TestFormatRate`           |     4 | Formateo de floats (trailing zeros, precisión)           |
| `TestFetchAllMeters`       |     2 | Fetch con mocks (página única, paginación)               |
| `TestExternalPrices`       |     9 | Precios externos: presencia, URLs, merge, precedencia    |

Ejecutar:

```bash
python -m pytest tests/test_update_prices.py -v
```

---

## Dependencias

- `requests` — llamadas HTTP a la Azure Retail Prices API
- `pyyaml` — parsing y generación de YAML

Ambas ya están en `requirements.txt`.

---

## Notas sobre el API de Azure Retail Prices

| Detalle                 | Valor                                                                     |
|-------------------------|---------------------------------------------------------------------------|
| URL base                | `https://prices.azure.com/api/retail/prices`                              |
| Autenticación           | Ninguna (pública)                                                        |
| Nombre de servicio      | `Foundry Models` (no ~~`Azure OpenAI`~~)                                 |
| Nombres de producto     | `Azure OpenAI`, `Azure OpenAI GPT5`, `Azure OpenAI Media`, `Azure OpenAI Reasoning`, `Azure OpenAI OSS Models` |
| Convenciones de nombres | Abreviados: `Gl`=Global, `Dz`=DataZone, `cchd`=cached, `inp`=input, `opt`/`out`=output, `aud`=audio, `rt`=realtime, `mn`=mini |
| Paginación              | `NextPageLink` en la respuesta JSON                                      |
| Filtro OData            | `$filter=contains(productName,'OpenAI') and isPrimaryMeterRegion eq true` |
