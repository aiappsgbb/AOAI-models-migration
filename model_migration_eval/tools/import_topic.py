#!/usr/bin/env python3
"""
import_topic.py — Importar un tema externo en la solución de evaluación
=======================================================================

Crea un tema archivado dentro de la estructura de la solución
(prompts/topics/{slug}/ y data/synthetic/topics/{slug}/) a partir de:
  - Un nombre de tema
  - Uno o mas ficheros de texto con prompts GPT-4
  - Uno o mas ficheros JSON de datos de prueba

El tipo de tarea se infiere automaticamente de los parametros utilizados:
  --gpt4-class-prompt         ->  classification
  --gpt4-dialog-prompt        ->  dialog
  --gpt4-rag-prompt           ->  rag
  --gpt4-tool-calling-prompt  ->  tool_calling

El tema queda listo para activarse desde la interfaz web y ejecutar
evaluaciones y comparaciones exactamente igual que cualquier otro tema
generado por la solución.

Pasos que realiza:
  1. Valida cada prompt GPT-4 suministrado, asegurando que tiene el formato
     de salida requerido por el pipeline de evaluación (lo añade si falta).
  2. Genera un prompt optimizado para GPT-5 por cada prompt GPT-4.
  3. Valida los datos de prueba y completa campos opcionales que falten.
  4. Escribe todo como un tema archivado en la estructura de la solución.

NO modifica los ficheros activos, NO ejecuta evaluaciones, NO genera
informes.  Todo eso se hace después desde la interfaz web.

Uso
---
    # Tema con prompt de clasificación y tres ficheros de datos
    python tools/import_topic.py ^
        --topic "Insurance Claims Processing" ^
        --gpt4-class-prompt my_cls_prompt.txt ^
        --class-test-data classification_data.json ^
        --dialog-test-data dialog_data.json ^
        --general-test-data general_data.json

    # Solo prompt de diálogo con datos de diálogo
    python tools/import_topic.py ^
        --topic "Hotel Concierge" ^
        --gpt4-dialog-prompt hotel_prompt.txt ^
        --dialog-test-data hotel_scenarios.json

    # Ambos prompts con datos mixtos
    python tools/import_topic.py ^
        --topic "Retail Support" ^
        --gpt4-class-prompt retail_cls.txt ^
        --gpt4-dialog-prompt retail_dlg.txt ^
        --class-test-data retail_cls.json ^
        --general-test-data retail_general.json

    # Todos los prompts y datos
    python tools/import_topic.py ^
        --topic "Full Service Desk" ^
        --gpt4-class-prompt cls.txt ^
        --gpt4-dialog-prompt dlg.txt ^
        --gpt4-rag-prompt rag.txt ^
        --gpt4-tool-calling-prompt tc.txt ^
        --class-test-data cls.json ^
        --dialog-test-data dlg.json ^
        --general-test-data gen.json ^
        --rag-test-data rag.json ^
        --tool-calling-test-data tc.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure the project root is importable regardless of cwd
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.clients.azure_openai import create_client_from_config, AzureOpenAIClient
from src.utils.prompt_manager import _extract_categories_from_prompt, _slugify

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIG_PATH = str(_PROJECT_ROOT / "config" / "settings.yaml")
GENERATOR_MODEL = "gpt5"

TASK_PROMPT_MAP = {
    "classification": "classification_agent_system",
    "dialog":         "dialog_agent_system",
    "rag":            "rag_agent_system",
    "tool_calling":   "tool_calling_agent_system",
}

DATA_FILE_MAP = {
    "classification": "classification_scenarios.json",
    "dialog":         "follow_up_scenarios.json",
    "general":        "capability_tests.json",
    "rag":            "rag_scenarios.json",
    "tool_calling":   "tool_calling_scenarios.json",
}

# Archive directories inside the solution
PROMPTS_TOPICS_DIR = _PROJECT_ROOT / "prompts" / "topics"
DATA_TOPICS_DIR    = _PROJECT_ROOT / "data" / "synthetic" / "topics"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("import_topic")


# ===================================================================
# GPT-4 prompt validation — ensure evaluation-compatible output format
# ===================================================================

_CLASSIFICATION_OUTPUT_BLOCK = """

---
## MANDATORY OUTPUT FORMAT

You MUST return a single JSON object (no markdown fences, no extra text) with
this exact structure:

```json
{
  "category": "<primary_category_code>",
  "subcategory": "<subcategory_code>",
  "priority": "<low|medium|high|critical>",
  "sentiment": "<sentiment_value>",
  "confidence": <0.0-1.0>,
  "entities": {
    "names": [],
    "ids": [],
    "amounts": [],
    "dates": [],
    "products": [],
    "other": []
  },
  "follow_up_questions": [],
  "reasoning_summary": "<one-sentence explanation>"
}
```

Return ONLY this JSON object. No additional text before or after.
"""

_DIALOG_OUTPUT_BLOCK = """

---
## RESPONSE GUIDELINES

When responding to the customer:
1. Begin with an empathetic acknowledgment of the customer's situation.
2. Address the specific question or concern directly.
3. Ask targeted follow-up questions to fill any information gaps.
4. Provide actionable next steps where possible.
5. Maintain a professional, warm tone throughout.
"""

_RAG_OUTPUT_BLOCK = """

---
## RAG RESPONSE GUIDELINES

When answering based on retrieved context:
1. Ground ALL claims in the provided context — cite specific passages.
2. If the context does not contain sufficient information, state that explicitly.
3. NEVER fabricate or hallucinate information beyond the context.
4. Synthesize information from multiple context passages when relevant.
5. Indicate confidence level when context is ambiguous or incomplete.
"""

_TOOL_CALLING_OUTPUT_BLOCK = """

---
## TOOL CALLING GUIDELINES

When selecting and invoking tools:
1. Select the most appropriate tool(s) based on the user's request.
2. Extract ALL required parameters from the user's message.
3. If required parameters are missing, ask the user before calling the tool.
4. Chain tool calls in the correct order when multiple steps are needed.
5. Explain what each tool call will accomplish before executing it.
"""


def _ensure_output_format(prompt: str, task: str) -> str:
    """Asegura que el prompt GPT-4 contiene el bloque de formato de salida
    necesario para que el pipeline de evaluación funcione."""

    if task == "classification":
        # ¿Ya define una estructura JSON de salida?
        if '"category"' in prompt and '"subcategory"' in prompt and '"priority"' in prompt:
            log.info("El prompt GPT-4 ya incluye definición de formato JSON de salida.")
            return prompt
        if re.search(r"(primary_category|category|subcategory|priority|sentiment)", prompt, re.I) \
                and "json" in prompt.lower():
            log.info("El prompt GPT-4 parece definir formato de salida — se mantiene tal cual.")
            return prompt
        log.warning("El prompt GPT-4 no tiene formato de salida JSON explícito — se añade el bloque requerido.")
        return prompt.rstrip() + "\n" + _CLASSIFICATION_OUTPUT_BLOCK

    elif task == "dialog":
        if re.search(r"(follow.?up|context.?gap|question)", prompt, re.I):
            log.info("El prompt GPT-4 de diálogo ya contiene guías de follow-up.")
            return prompt
        log.warning("El prompt GPT-4 de diálogo no tiene guías de follow-up — se añade bloque.")
        return prompt.rstrip() + "\n" + _DIALOG_OUTPUT_BLOCK

    elif task == "rag":
        if re.search(r"(ground|context|retriev|cite|passage)", prompt, re.I):
            log.info("El prompt GPT-4 de RAG ya contiene guías de grounding.")
            return prompt
        log.warning("El prompt GPT-4 de RAG no tiene guías de grounding — se añade bloque.")
        return prompt.rstrip() + "\n" + _RAG_OUTPUT_BLOCK

    elif task == "tool_calling":
        if re.search(r"(tool|function|parameter|invoke|call)", prompt, re.I):
            log.info("El prompt GPT-4 de tool_calling ya contiene guías de tools.")
            return prompt
        log.warning("El prompt GPT-4 de tool_calling no tiene guías de tools — se añade bloque.")
        return prompt.rstrip() + "\n" + _TOOL_CALLING_OUTPUT_BLOCK

    # General: no requiere formato especial
    return prompt


# ===================================================================
# GPT-5 prompt generation
# ===================================================================

_MODEL_GUIDANCE_GPT5 = (
    "GPT-5.x best practices:\n"
    "- Leverage native reasoning (no explicit CoT needed)\n"
    "- Use YAML-based schema definitions for structure\n"
    "- Streamlined, concise instructions\n"
    "- Use <system_configuration> blocks for model params\n"
    "- Specify reasoning_effort level\n"
    "- Focus on WHAT not HOW — the model figures out the approach\n"
    "- Use max_completion_tokens instead of max_tokens"
)


def _build_gpt5_generation_meta_prompt(
    topic: str,
    task: str,
    gpt4_prompt: str,
    categories: Optional[List[str]] = None,
) -> str:
    """Meta-prompt para que la IA genere el prompt GPT-5 a partir del GPT-4."""

    task_descriptions = {
        "classification": (
            "a CLASSIFICATION system prompt that:\n"
            "- Defines categories, subcategories, priority levels, and sentiments\n"
            "- Produces structured JSON output\n"
            "- Includes entity extraction (names, IDs, amounts, dates)\n"
            "- Generates appropriate follow-up questions\n"
            "- Adapts the taxonomy and examples to the given TOPIC"
        ),
        "dialog": (
            "a DIALOG / CONVERSATION AGENT system prompt that:\n"
            "- Guides multi-turn conversations with context tracking\n"
            "- Identifies information gaps and asks targeted follow-up questions\n"
            "- Maintains professional tone appropriate to the topic\n"
            "- Handles escalation and resolution flows\n"
            "- Adapts conversation style and knowledge to the given TOPIC"
        ),
        "rag": (
            "a RAG (Retrieval-Augmented Generation) system prompt that:\n"
            "- Grounds all responses in provided context passages\n"
            "- Refuses to hallucinate beyond available evidence\n"
            "- Cites relevant passages or sections\n"
            "- Handles conflicting or incomplete context gracefully\n"
            "- Adapts domain knowledge to the given TOPIC"
        ),
        "tool_calling": (
            "a TOOL CALLING / FUNCTION CALLING agent system prompt that:\n"
            "- Selects appropriate tools based on user intent\n"
            "- Extracts required parameters accurately from queries\n"
            "- Chains multiple tool calls when needed\n"
            "- Handles missing parameters by asking clarifying questions\n"
            "- Adapts tool usage patterns to the given TOPIC"
        ),
    }

    cat_block = ""
    if categories:
        cat_list = "\n".join(f"  - {c}" for c in categories)
        cat_block = (
            f"\n## MANDATORY CATEGORY TAXONOMY (CRITICAL — DO NOT CHANGE)\n"
            f"You MUST use EXACTLY these primary category codes.\n"
            f"Copy each code CHARACTER-BY-CHARACTER — do NOT rename, paraphrase,\n"
            f"merge, split, abbreviate, or invent new categories:\n\n"
            f"{cat_list}\n\n"
            f"These are the ONLY valid primary_category values.\n"
            f"You may freely create subcategories, descriptions, and examples\n"
            f"adapted to this model's style, but the primary category codes\n"
            f"MUST be identical to the list above.\n"
        )

    return f"""Generate a production-ready system prompt for the following scenario:

## TOPIC
{topic}

## TARGET MODEL
GPT-5 — follow these guidelines:
{_MODEL_GUIDANCE_GPT5}

## TASK TYPE
Create {task_descriptions.get(task, task)}

## REFERENCE (GPT-4 prompt for the SAME topic — adapt the STYLE to GPT-5
best practices but keep the EXACT SAME primary category codes and domain
knowledge)
{gpt4_prompt[:6000]}

## REQUIREMENTS
1. The prompt must be fully self-contained (no placeholders left)
2. Keep EXACTLY the same primary category codes as the reference — do NOT
   rename, merge, split, or invent new categories
3. Adapt subcategories, descriptions, examples, and formatting to the GPT-5
   style guidelines above
4. Keep the same structural quality as the reference
5. The JSON output schema MUST be compatible with the reference prompt's schema
   (same field names: category, subcategory, priority, sentiment, confidence,
   entities, follow_up_questions, reasoning_summary)
6. Output ONLY the system prompt content — no wrapper, no explanation
{cat_block}
"""


def _fix_gpt5_categories(
    client: AzureOpenAIClient,
    gpt5_prompt: str,
    target_categories: List[str],
    generator_model: str,
) -> str:
    """Regenera el prompt GPT-5 con enforcement más estricto de las categorías."""
    fix_prompt = (
        "The following system prompt was generated for a GPT-5 classification agent, "
        "but it uses WRONG category codes. Rewrite it so that the EXACT primary "
        "category codes listed below are used AS-IS (copy verbatim, do NOT rename).\n\n"
        "MANDATORY PRIMARY CATEGORY CODES (use these EXACTLY):\n"
        + "\n".join(f"  - {c}" for c in target_categories)
        + "\n\nOriginal prompt to fix:\n" + gpt5_prompt[:6000]
        + "\n\nReturn ONLY the corrected system prompt. Keep the same structure, "
        "descriptions, and subcategory style — just replace ALL primary category "
        "codes with the mandatory ones above."
    )
    res = client.complete(
        messages=[
            {"role": "system", "content": "You are an expert prompt engineer. Fix the category codes as instructed. Output ONLY the corrected prompt."},
            {"role": "user", "content": fix_prompt},
        ],
        model_name=generator_model,
    )
    fixed = res.content.strip()
    fixed_cats = _extract_categories_from_prompt(fixed)
    overlap = set(fixed_cats) & set(target_categories)
    if len(overlap) >= len(target_categories) * 0.5:
        log.info(f"Auto-fix de categorías exitoso: {len(overlap)}/{len(target_categories)} alineadas.")
        return fixed
    log.warning("Auto-fix no mejoró el solapamiento — se usa el prompt GPT-5 original.")
    return gpt5_prompt


def generate_gpt5_prompt(
    client: AzureOpenAIClient,
    topic: str,
    task: str,
    gpt4_prompt: str,
    generator_model: str,
) -> str:
    """Llama al modelo generador para producir la versión GPT-5 del prompt."""

    categories = None
    if task == "classification":
        categories = _extract_categories_from_prompt(gpt4_prompt)
        if categories:
            log.info(f"Categorías extraídas del prompt GPT-4 ({len(categories)}): {categories}")
        else:
            log.warning("No se pudieron extraer categorías del prompt GPT-4 — GPT-5 definirá las suyas.")

    meta_prompt = _build_gpt5_generation_meta_prompt(topic, task, gpt4_prompt, categories)

    log.info("Generando prompt GPT-5 mediante IA...")
    t0 = time.time()
    res = client.complete(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert prompt engineer specialising in Azure OpenAI models. "
                    "You create high-quality system prompts that follow each model family's "
                    "best practices.  Return ONLY the system prompt content, no explanations, "
                    "no markdown fences."
                ),
            },
            {"role": "user", "content": meta_prompt},
        ],
        model_name=generator_model,
    )
    elapsed = time.time() - t0
    generated = res.content.strip()
    log.info(f"Prompt GPT-5 generado en {elapsed:.1f}s  ({len(generated)} chars)")

    # Validar que GPT-5 reutiliza las mismas categorías
    if categories:
        gpt5_cats = _extract_categories_from_prompt(generated)
        overlap = set(gpt5_cats) & set(categories)
        if len(overlap) < len(categories) * 0.5:
            log.warning(
                f"Prompt GPT-5 tiene bajo solapamiento de categorías ({len(overlap)}/{len(categories)}). "
                "Intentando auto-fix..."
            )
            generated = _fix_gpt5_categories(client, generated, categories, generator_model)

    return generated


# ===================================================================
# Test data validation
# ===================================================================

def validate_and_fix_test_data(data: list, task: str) -> List[str]:
    """Valida que los datos de prueba tengan el esquema esperado por el
    framework.  Completa campos opcionales que falten.
    Devuelve lista de warnings (vacía = todo OK)."""

    warnings: List[str] = []
    if not isinstance(data, list):
        warnings.append("Los datos de prueba deben ser un array JSON.")
        return warnings
    if len(data) == 0:
        warnings.append("Los datos de prueba están vacíos.")
        return warnings

    sample = data[0]

    if task == "classification":
        required = {"id", "customer_input", "expected_category"}
        missing = required - set(sample.keys())
        if missing:
            warnings.append(f"Campos obligatorios ausentes en classification: {missing}")
        for i, item in enumerate(data):
            if "id" not in item:
                item["id"] = f"CLASS_{i+1:03d}"
            if "scenario" not in item:
                item["scenario"] = item.get("id", f"scenario_{i+1}")
            for field in ("expected_subcategory", "expected_priority", "expected_sentiment"):
                if field not in item:
                    item[field] = ""
            if "context" not in item:
                item["context"] = {}
            if "follow_up_questions_expected" not in item:
                item["follow_up_questions_expected"] = []

    elif task == "dialog":
        required = {"id", "conversation", "context_gaps"}
        missing = required - set(sample.keys())
        if missing:
            warnings.append(f"Campos obligatorios ausentes en dialog: {missing}")
        for i, item in enumerate(data):
            if "id" not in item:
                item["id"] = f"DLG_{i+1:03d}"
            if "scenario" not in item:
                item["scenario"] = item.get("id", f"scenario_{i+1}")
            if "optimal_follow_up" not in item:
                item["optimal_follow_up"] = ""
            if "follow_up_rules" not in item:
                item["follow_up_rules"] = []
            if "expected_resolution_turns" not in item:
                item["expected_resolution_turns"] = 2
            if "category" not in item:
                item["category"] = "general"

    elif task == "rag":
        required = {"id", "query", "context", "ground_truth"}
        missing = required - set(sample.keys())
        if missing:
            warnings.append(f"Campos obligatorios ausentes en rag: {missing}")
        for i, item in enumerate(data):
            if "id" not in item:
                item["id"] = f"RAG_{i+1:03d}"
            if "scenario" not in item:
                item["scenario"] = item.get("id", f"rag_scenario_{i+1}")
            if "expected_behavior" not in item:
                item["expected_behavior"] = "grounded_answer"
            if "complexity" not in item:
                item["complexity"] = "medium"

    elif task == "tool_calling":
        required = {"id", "query", "available_tools", "expected_tool_calls"}
        missing = required - set(sample.keys())
        if missing:
            warnings.append(f"Campos obligatorios ausentes en tool_calling: {missing}")
        for i, item in enumerate(data):
            if "id" not in item:
                item["id"] = f"TC_{i+1:03d}"
            if "scenario" not in item:
                item["scenario"] = item.get("id", f"tool_scenario_{i+1}")
            if "expected_parameters" not in item:
                item["expected_parameters"] = {}
            if "complexity" not in item:
                item["complexity"] = "medium"

    elif task == "general":
        required = {"id", "test_type"}
        missing = required - set(sample.keys())
        if missing:
            warnings.append(f"Campos obligatorios ausentes en general: {missing}")
        for i, item in enumerate(data):
            if "id" not in item:
                item["id"] = f"GEN_{i+1:03d}"
            if "complexity" not in item:
                item["complexity"] = "medium"
            if "prompt" not in item and "conversation" not in item:
                warnings.append(f"Item {i} no tiene ni 'prompt' ni 'conversation'.")

    return warnings


# ===================================================================
# Write archived topic into the solution
# ===================================================================

def write_archived_topic(
    slug: str,
    topic_name: str,
    prompts_map: Dict[str, tuple],
    test_data_map: Dict[str, list],
) -> Path:
    """Escribe el tema como un topic archivado en la estructura de la
    solución, listo para ser activado desde la interfaz web.

    Args:
        slug: Identificador del tema (filesystem-safe).
        topic_name: Nombre legible del tema.
        prompts_map: Dict con claves de tarea (classification, dialog,
                     rag, tool_calling) mapeando a tuplas
                     (gpt4_content, gpt5_content).
        test_data_map: Dict con hasta 5 claves (classification, dialog,
                       general, rag, tool_calling) mapeando a la lista
                       de escenarios de cada tipo de tarea.

    Estructura creada:
        prompts/topics/{slug}/
        ├── gpt4/{task}_agent_system.md   (por cada tarea en prompts_map)
        ├── gpt5/{task}_agent_system.md   (por cada tarea en prompts_map)
        └── topic.json

        data/synthetic/topics/{slug}/
        ├── classification/classification_scenarios.json   (si se proporciono)
        ├── dialog/follow_up_scenarios.json                (si se proporciono)
        ├── general/capability_tests.json                  (si se proporciono)
        ├── rag/rag_scenarios.json                         (si se proporciono)
        └── tool_calling/tool_calling_scenarios.json       (si se proporciono)
    """

    prompt_topic_dir = PROMPTS_TOPICS_DIR / slug
    data_topic_dir   = DATA_TOPICS_DIR / slug

    # --- Prompts (one pair per task type provided) ---
    for task, (gpt4_content, gpt5_content) in prompts_map.items():
        prompt_type = TASK_PROMPT_MAP.get(task)
        if not prompt_type:
            log.warning(f"Tipo de tarea desconocido para prompts: {task} — se omite.")
            continue
        for model, content in (("gpt4", gpt4_content), ("gpt5", gpt5_content)):
            model_dir = prompt_topic_dir / model
            model_dir.mkdir(parents=True, exist_ok=True)
            prompt_file = model_dir / f"{prompt_type}.md"
            prompt_file.write_text(content, encoding="utf-8")
            log.info(f"  Prompt {model}/{prompt_type}.md  ({len(content)} chars)")

    # --- Test data (one file per task type provided) ---
    for data_task, data_items in test_data_map.items():
        data_filename = DATA_FILE_MAP[data_task]
        data_subdir = data_topic_dir / data_task
        data_subdir.mkdir(parents=True, exist_ok=True)
        data_file = data_subdir / data_filename
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(data_items, f, indent=2, ensure_ascii=False)
        log.info(f"  Datos {data_task}/{data_filename}  ({len(data_items)} items)")

    # --- topic.json ---
    now = datetime.now().isoformat()
    meta = {
        "topic": topic_name,
        "slug": slug,
        "archived_at": now,
        "prompts_updated_at": now,
        "data_generated_at": now,
    }
    meta_file = prompt_topic_dir / "topic.json"
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"  Metadata topic.json")

    return prompt_topic_dir


# ===================================================================
# Main
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Importa un tema externo (prompts GPT-4 + datos de prueba) como "
            "un tema archivado en la solución de evaluación, generando "
            "automáticamente los prompts optimizados para GPT-5.\n\n"
            "El tipo de tarea se infiere del parámetro de prompt utilizado:\n"
            "  --gpt4-class-prompt  → classification\n"
            "  --gpt4-dialog-prompt → dialog"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--topic", required=True,
        help="Nombre legible del tema (ej: 'Insurance Claims Processing').",
    )

    # --- GPT-4 prompts (at least one required) ---
    prompt_group = parser.add_argument_group(
        "GPT-4 prompts",
        "Al menos uno es obligatorio. Se puede proporcionar uno o ambos.",
    )
    prompt_group.add_argument(
        "--gpt4-class-prompt", type=Path, default=None,
        help="Fichero de texto (.txt o .md) con el prompt de sistema GPT-4 de clasificación.",
    )
    prompt_group.add_argument(
        "--gpt4-dialog-prompt", type=Path, default=None,
        help="Fichero de texto (.txt o .md) con el prompt de sistema GPT-4 de diálogo.",
    )
    prompt_group.add_argument(
        "--gpt4-rag-prompt", type=Path, default=None,
        help="Fichero de texto (.txt o .md) con el prompt de sistema GPT-4 de RAG.",
    )
    prompt_group.add_argument(
        "--gpt4-tool-calling-prompt", type=Path, default=None,
        help="Fichero de texto (.txt o .md) con el prompt de sistema GPT-4 de tool calling.",
    )

    # --- Test data files (at least one required) ---
    data_group = parser.add_argument_group(
        "Datos de prueba",
        "Al menos uno es obligatorio. Se pueden proporcionar hasta cinco.",
    )
    data_group.add_argument(
        "--class-test-data", type=Path, default=None,
        help="Fichero JSON con escenarios de clasificación (classification_scenarios).",
    )
    data_group.add_argument(
        "--dialog-test-data", type=Path, default=None,
        help="Fichero JSON con escenarios de diálogo (follow_up_scenarios).",
    )
    data_group.add_argument(
        "--general-test-data", type=Path, default=None,
        help="Fichero JSON con tests de capacidad general (capability_tests).",
    )
    data_group.add_argument(
        "--rag-test-data", type=Path, default=None,
        help="Fichero JSON con escenarios RAG (rag_scenarios).",
    )
    data_group.add_argument(
        "--tool-calling-test-data", type=Path, default=None,
        help="Fichero JSON con escenarios de tool calling (tool_calling_scenarios).",
    )

    # --- Optional settings ---
    parser.add_argument(
        "--config", type=str, default=CONFIG_PATH,
        help="Ruta a settings.yaml (por defecto: config/settings.yaml).",
    )
    parser.add_argument(
        "--generator-model", type=str, default=GENERATOR_MODEL,
        help=f"Modelo para generar el prompt GPT-5 (por defecto: {GENERATOR_MODEL}).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Sobrescribir si el tema ya existe como archivo.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Activar logging en modo DEBUG.",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # --- Verify at least one GPT-4 prompt is provided ---
    gpt4_prompt_args = {
        "classification": args.gpt4_class_prompt,
        "dialog":         args.gpt4_dialog_prompt,
        "rag":            args.gpt4_rag_prompt,
        "tool_calling":   args.gpt4_tool_calling_prompt,
    }
    if not any(gpt4_prompt_args.values()):
        parser.error(
            "Debes proporcionar al menos un prompt GPT-4.\n"
            "  Usa --gpt4-class-prompt, --gpt4-dialog-prompt, --gpt4-rag-prompt\n"
            "  y/o --gpt4-tool-calling-prompt."
        )

    # --- Verify at least one test data file is provided ---
    test_data_args = {
        "classification": args.class_test_data,
        "dialog":         args.dialog_test_data,
        "general":        args.general_test_data,
        "rag":            args.rag_test_data,
        "tool_calling":   args.tool_calling_test_data,
    }
    if not any(test_data_args.values()):
        parser.error(
            "Debes proporcionar al menos un fichero de datos de prueba.\n"
            "  Usa --class-test-data, --dialog-test-data, --general-test-data,\n"
            "  --rag-test-data y/o --tool-calling-test-data."
        )

    slug = _slugify(args.topic)

    # --- Banner ---
    prompt_tasks = ", ".join(t for t, p in gpt4_prompt_args.items() if p)
    data_summary = ", ".join(t for t, p in test_data_args.items() if p)
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        Import Topic — Model Migration Evaluation             ║
╠══════════════════════════════════════════════════════════════╣
║  Topic:    {args.topic:<49s}║
║  Prompts:  {prompt_tasks:<49s}║
║  Slug:     {slug:<49s}║
║  Data:     {data_summary:<49s}║
╚══════════════════════════════════════════════════════════════╝
""")

    # --- Check if topic already exists ---
    existing = PROMPTS_TOPICS_DIR / slug
    if existing.exists() and not args.force:
        log.error(
            f"El tema '{slug}' ya existe en {existing}.\n"
            f"  Usa --force para sobrescribirlo."
        )
        sys.exit(1)

    # --- Load and validate test data files ---
    test_data_map: Dict[str, list] = {}
    for data_task, data_path in test_data_args.items():
        if data_path is None:
            continue
        log.info(f"Cargando datos de prueba ({data_task})...")
        if not data_path.exists():
            log.error(f"Fichero de datos no encontrado: {data_path}")
            sys.exit(1)
        with open(data_path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        # Handle wrapped format {"scenarios": [...]}
        if isinstance(raw, dict):
            raw = (
                raw.get("scenarios")
                or next((v for v in raw.values() if isinstance(v, list)), [])
            )
        log.info(f"  {len(raw)} escenarios desde {data_path}")

        # Validate
        log.info(f"Validando esquema de datos ({data_task})...")
        warnings = validate_and_fix_test_data(raw, data_task)
        if warnings:
            for w in warnings:
                log.warning(f"  [!] {w}")
            if any("obligatorios" in w for w in warnings):
                log.error(f"Los datos de {data_task} tienen problemas críticos de esquema. Abortando.")
                sys.exit(1)
        else:
            log.info(f"  [OK] Esquema de datos {data_task} valido.")
        test_data_map[data_task] = raw

    # --- Create Azure OpenAI client ---
    log.info("Creando cliente Azure OpenAI...")
    try:
        client = create_client_from_config(args.config)
    except Exception as e:
        log.error(f"No se pudo crear el cliente: {e}")
        sys.exit(1)

    # --- Load, validate and generate GPT-5 for each prompt (PARALLEL) ---
    prompts_map: Dict[str, tuple] = {}

    # First pass: load and validate all GPT-4 prompts (fast, no LLM)
    gpt4_contents: Dict[str, str] = {}
    for task, prompt_path in gpt4_prompt_args.items():
        if prompt_path is None:
            continue
        log.info(f"Cargando prompt GPT-4 ({task})...")
        if not prompt_path.exists():
            log.error(f"Fichero de prompt no encontrado: {prompt_path}")
            sys.exit(1)
        gpt4_raw = prompt_path.read_text(encoding="utf-8")
        log.info(f"  {len(gpt4_raw)} caracteres desde {prompt_path}")
        log.info(f"Validando prompt GPT-4 ({task}) para compatibilidad con evaluacion...")
        gpt4_contents[task] = _ensure_output_format(gpt4_raw, task)

    # Second pass: generate ALL GPT-5 prompts in parallel via async
    async def _generate_all_gpt5():
        sem = asyncio.Semaphore(5)
        async def _gen_one(task_name, gpt4_content):
            async with sem:
                log.info(f"[parallel] Generando prompt GPT-5 ({task_name})...")
                t0 = time.time()
                categories = None
                if task_name == "classification":
                    categories = _extract_categories_from_prompt(gpt4_content)
                meta_prompt = _build_gpt5_generation_meta_prompt(
                    args.topic, task_name, gpt4_content, categories,
                )
                res = await client.complete_async(
                    messages=[
                        {"role": "system", "content": (
                            "You are an expert prompt engineer specialising in Azure OpenAI models. "
                            "You create high-quality system prompts that follow each model family's "
                            "best practices.  Return ONLY the system prompt content, no explanations, "
                            "no markdown fences."
                        )},
                        {"role": "user", "content": meta_prompt},
                    ],
                    model_name=args.generator_model,
                )
                generated = res.content.strip()
                elapsed = time.time() - t0
                log.info(f"[parallel] GPT-5 ({task_name}) generado en {elapsed:.1f}s ({len(generated)} chars)")

                # Category validation for classification
                if categories:
                    gpt5_cats = _extract_categories_from_prompt(generated)
                    overlap = set(gpt5_cats) & set(categories)
                    if len(overlap) < len(categories) * 0.5:
                        log.warning(f"Bajo solapamiento categorias GPT-5 ({task_name}). Auto-fix...")
                        generated = _fix_gpt5_categories(
                            client, generated, categories, args.generator_model,
                        )
                return (task_name, generated)

        tasks = [_gen_one(t, c) for t, c in gpt4_contents.items()]
        return await asyncio.gather(*tasks)

    if gpt4_contents:
        t0 = time.time()
        log.info(f"Generando {len(gpt4_contents)} prompts GPT-5 en paralelo...")
        gpt5_results = asyncio.run(_generate_all_gpt5())
        elapsed = time.time() - t0
        log.info(f"Todos los prompts GPT-5 generados en {elapsed:.1f}s (paralelo)")

        for task_name, gpt5_content in gpt5_results:
            prompts_map[task_name] = (gpt4_contents[task_name], gpt5_content)

    # --- Write archived topic ---
    log.info("Escribiendo tema archivado en la solución...")
    topic_dir = write_archived_topic(
        slug=slug,
        topic_name=args.topic,
        prompts_map=prompts_map,
        test_data_map=test_data_map,
    )

    # --- Done ---
    prompt_lines = []
    for task in prompts_map:
        prompt_type = TASK_PROMPT_MAP.get(task, task)
        prompt_lines.append(f"    {prompt_type}  (gpt4 + gpt5)")
    prompt_block = "\n".join(prompt_lines) if prompt_lines else "    (ninguno)"

    data_lines = []
    for dt, items in test_data_map.items():
        data_lines.append(f"    {dt}: {len(items)} escenarios")
    data_block = "\n".join(data_lines) if data_lines else "    (ninguno)"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                        ✓  COMPLETADO                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Tema archivado correctamente.                               ║
║                                                              ║
║  Prompts:  prompts/topics/{slug + '/':<38s}║
║  Datos:    data/synthetic/topics/{slug + '/':<28s}║
║                                                              ║
║  Prompts generados:                                          ║
""")
    for line in prompt_lines:
        print(f"║  {line:<58s}║")
    print(f"""\
║                                                              ║
║  Datos cargados:                                             ║
""")
    for line in data_lines:
        print(f"║  {line:<58s}║")
    print(f"""\
║                                                              ║
║  Próximos pasos:                                             ║
║    1. Abre la interfaz web:  python app.py                   ║
║    2. Ve a la sección de temas                               ║
║    3. Activa el tema "{slug}"
║    4. Ejecuta evaluaciones y comparaciones normalmente       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
