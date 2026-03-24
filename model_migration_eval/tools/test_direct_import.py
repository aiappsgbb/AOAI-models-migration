#!/usr/bin/env python3
"""Direct import test — bypasses Flask test client to avoid timeout issues."""
import sys, os, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from import_topic import (
    _ensure_output_format,
    generate_target_prompt,
    _resolve_model_family,
    _inject_missing_categories,
    _extract_json_fields_from_dialog,
    validate_and_fix_test_data,
    write_archived_topic,
    TASK_PROMPT_MAP,
    DATA_FILE_MAP,
)
from src.utils.prompt_manager import _slugify
from src.utils.category_parser import extract_categories_from_prompt
from src.clients.azure_openai_client import AzureOpenAIClient


def main():
    topic_name = "test_import_telco_customer_service"
    source_model = "gpt4o"
    generator_model = "gpt5"
    slug = _slugify(topic_name)

    # ── Source paths ──
    src_dir = Path(ROOT) / "prompts" / "topics" / "telco_customer_service" / source_model
    data_dir = Path(ROOT) / "data" / "synthetic" / "topics" / "telco_customer_service"

    # ── Read source prompts ──
    prompt_files = {}
    for task, fname in TASK_PROMPT_MAP.items():
        p = src_dir / fname
        if p.exists():
            prompt_files[task] = p.read_text(encoding='utf-8')
            print(f"  ✓ {task}: {fname} ({len(prompt_files[task]):,} chars)")

    # ── Read test data ──
    data_files = {}
    for task, fname in DATA_FILE_MAP.items():
        p = data_dir / task / fname
        if p.exists():
            data_files[task] = json.loads(p.read_text(encoding='utf-8'))
            print(f"  ✓ {task} data: {fname} ({len(data_files[task])} scenarios)")

    # ── Validate prompts ──
    validated = {}
    for task, raw in prompt_files.items():
        validated[task] = _ensure_output_format(raw, task)

    # ── Extract domain context ──
    domain_categories = None
    if 'classification' in validated:
        domain_categories = extract_categories_from_prompt(validated['classification'])
        print(f"\n  Domain categories ({len(domain_categories)}): {domain_categories}")

    dialog_json_fields = None
    if 'dialog' in validated:
        dialog_json_fields = _extract_json_fields_from_dialog(validated['dialog'])
        print(f"  Dialog JSON fields ({len(dialog_json_fields or [])}): {dialog_json_fields}")

    domain_tools_summary = None
    if 'tool_calling' in data_files:
        tool_names = set()
        for sc in data_files['tool_calling']:
            raw_tools = sc.get('available_tools', [])
            if isinstance(raw_tools, str):
                try: raw_tools = json.loads(raw_tools)
                except: raw_tools = []
            if isinstance(raw_tools, list):
                for t in raw_tools:
                    if isinstance(t, dict):
                        n = t.get('function', {}).get('name') or t.get('name', '')
                    elif isinstance(t, str):
                        n = t
                    else:
                        n = ''
                    if n: tool_names.add(n)
        if tool_names:
            domain_tools_summary = ', '.join(sorted(tool_names))
            print(f"  Tool names ({len(tool_names)}): {domain_tools_summary}")

    # ── Determine target models ──
    from src.clients.azure_openai_client import AzureOpenAIClient
    client = AzureOpenAIClient()
    all_models = list(client.deployments.keys()) if hasattr(client, 'deployments') else []
    
    # Use settings for target models
    from src.utils.settings import load_settings
    settings = load_settings()
    all_model_keys = settings.get('models', {}).keys() if settings else []
    model_families = {}
    model_deployments = {}
    for mk in all_model_keys:
        mcfg = settings['models'][mk]
        model_families[mk] = mcfg.get('family', _resolve_model_family(mk))
        model_deployments[mk] = mcfg.get('deployment_name')
    
    target_models = [m for m in all_model_keys if m != source_model]
    print(f"\n  Target models ({len(target_models)}): {target_models}")

    # ── Generate prompts ──
    def gen_one(task, src_content, tgt_model):
        family = model_families.get(tgt_model, _resolve_model_family(tgt_model))
        dep_name = model_deployments.get(tgt_model)
        print(f"  Generating {tgt_model} {task}...", flush=True)
        t0 = time.time()
        generated = generate_target_prompt(
            client, topic_name, task, src_content, generator_model,
            target_model=tgt_model, model_family=family,
            deployment_name=dep_name,
            domain_categories=domain_categories,
            domain_tools_summary=domain_tools_summary,
            dialog_json_fields=dialog_json_fields,
        )
        elapsed = round(time.time() - t0, 1)
        print(f"  ✓ {tgt_model} {task} ({elapsed}s, {len(generated):,} chars)", flush=True)
        return task, tgt_model, generated, elapsed

    print(f"\n{'='*60}")
    print(f"Generating prompts for {len(target_models)} models × {len(validated)} tasks...")
    print(f"{'='*60}")

    t_total = time.time()
    results = []
    tasks_to_run = [
        (task, content, tgt)
        for task, content in validated.items()
        for tgt in target_models
    ]

    with ThreadPoolExecutor(max_workers=len(tasks_to_run)) as pool:
        future_map = {
            pool.submit(gen_one, t, c, m): (t, m) for t, c, m in tasks_to_run
        }
        for future in as_completed(future_map):
            results.append(future.result())

    total_elapsed = round(time.time() - t_total, 1)
    print(f"\n  All generated in {total_elapsed}s")

    # ── Build prompts_map ──
    prompts_map = {}
    for task, tgt_model, content, elapsed in results:
        prompts_map.setdefault(task, {})[tgt_model] = content
    for task, content in validated.items():
        prompts_map.setdefault(task, {})[source_model] = content

    # ── Post-generation: deterministic category injection ──
    if domain_categories and 'classification' in prompts_map:
        source_cls = validated.get('classification', '')
        for tgt_model, tgt_content in list(prompts_map['classification'].items()):
            if tgt_model == source_model:
                continue
            patched = _inject_missing_categories(tgt_content, source_cls, domain_categories)
            if patched != tgt_content:
                prompts_map['classification'][tgt_model] = patched
                print(f"  ⚡ {tgt_model}: deterministic category injection applied")

    # ── Write topic ──
    user_id = "test_at_example_com"
    topics_dir = Path(ROOT) / "data" / "users" / user_id / "prompts" / "topics"
    data_topics_dir = Path(ROOT) / "data" / "users" / user_id / "synthetic" / "topics"

    write_archived_topic(
        slug=slug,
        topic_name=topic_name,
        prompts_map=prompts_map,
        test_data_map=data_files,
        prompts_topics_dir=str(topics_dir),
        data_topics_dir=str(data_topics_dir),
    )

    # ── Verify ──
    out_dir = topics_dir / slug
    files = list(out_dir.rglob("*"))
    files = [f for f in files if f.is_file()]
    print(f"\n{'='*60}")
    print(f"✅ Import complete! {len(files)} files written to {out_dir}")
    for f in sorted(files):
        print(f"  {f.relative_to(out_dir)} ({f.stat().st_size:,} bytes)")

    # ── Quick verification of other_or_unclear ──
    print(f"\n{'='*60}")
    print("Category verification:")
    for model in target_models:
        cls_path = out_dir / model / "classification_agent_system.md"
        if cls_path.exists():
            content = cls_path.read_text(encoding='utf-8')
            cats = extract_categories_from_prompt(content)
            has_oou = 'other_or_unclear' in cats
            oou_count = content.count('other_or_unclear')
            print(f"  {model}: {len(cats)} cats, other_or_unclear={'✓' if has_oou else '✗'} (appears {oou_count}x)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
