#!/usr/bin/env python3
"""Upload RAG pipeline evaluation results to Azure AI Foundry.

Reads the JSON results from data/results/, converts them to Foundry's
evaluation format, and creates labeled evaluation runs in the Foundry portal.

Each run is named with the model config + timestamp so you can compare
different migration scenarios side-by-side in the dashboard.

NOTE on score alignment:
    Local evaluation (test_e2e.py) uses a custom LLM-as-judge prompt that
    produces scores on a 1-5 scale for groundedness, relevance, correctness.
    Foundry built-in evaluators (builtin.coherence, builtin.groundedness, etc.)
    use Microsoft's standardized rubrics, which may produce slightly different
    scores. Both are valid — local scores are faster for dev iteration, Foundry
    scores provide standardized dashboards for cross-team comparison.
    Use local eval for rapid A/B during development; use Foundry for governance
    and stakeholder reporting.

Requirements:
    pip install "azure-ai-projects>=2.0.0" azure-identity python-dotenv

Usage:
    # Set env vars in .env:
    #   AZURE_AI_PROJECT_ENDPOINT=https://ACCOUNT.services.ai.azure.com/api/projects/PROJECT
    #   FOUNDRY_JUDGE_DEPLOYMENT=gpt-4o

    python samples/rag_pipeline/upload_to_foundry.py

    # Or specify results files directly:
    python samples/rag_pipeline/upload_to_foundry.py \\
        --source-results data/results/dual_layer_gpt-4o_*.json \\
        --target-results data/results/dual_layer_gpt-4.1_*.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from glob import glob
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")


def _find_latest_results(pattern: str) -> str | None:
    """Find the most recent results file matching a glob pattern."""
    files = sorted(glob(pattern))
    return files[-1] if files else None


def _load_dual_layer_json(path: str) -> dict:
    """Load a DualLayerReport JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_eval_dataset(report: dict, config_label: str) -> list[dict]:
    """Convert a DualLayerReport dict into Foundry evaluation rows.

    Each row has: query, response, context, ground_truth, config_label.
    """
    rows = []
    for e2e in report.get("end_to_end", []):
        row = {
            "query": e2e["query"],
            "response": e2e["answer"],
            "config": config_label,
        }
        # Add context from retrieval results if available
        for ret in report.get("retrieval", []):
            if ret["query"] == e2e["query"]:
                row["context"] = ", ".join(ret.get("retrieved_ids", []))
                break
        rows.append(row)
    return rows


def upload_to_foundry(
    source_path: str,
    target_path: str,
    project_endpoint: str | None = None,
    judge_deployment: str | None = None,
) -> None:
    """Upload evaluation results to Azure AI Foundry."""
    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from openai.types.eval_create_params import DataSourceConfigCustom
    from openai.types.evals.create_eval_jsonl_run_data_source_param import (
        CreateEvalJSONLRunDataSourceParam,
        SourceFileContent,
        SourceFileContentContent,
    )

    endpoint = project_endpoint or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT in .env or pass --project-endpoint")
        sys.exit(1)

    judge_model = judge_deployment or os.getenv("FOUNDRY_JUDGE_DEPLOYMENT", "gpt-4o")

    print(f"  Foundry endpoint: {endpoint}")
    print(f"  Judge deployment: {judge_model}")

    # Load results
    source_report = _load_dual_layer_json(source_path)
    target_report = _load_dual_layer_json(target_path)

    source_label = os.path.basename(source_path).split("_")[2]  # e.g. "gpt-4o"
    target_label = os.path.basename(target_path).split("_")[2]  # e.g. "gpt-4.1"

    print(f"  Source: {source_label} ({source_path})")
    print(f"  Target: {target_label} ({target_path})")

    # Build datasets
    source_rows = _build_eval_dataset(source_report, source_label)
    target_rows = _build_eval_dataset(target_report, target_label)

    # Connect to Foundry
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)
    client = project_client.get_openai_client()

    # Define evaluation schema
    data_source_config = DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "response": {"type": "string"},
                "context": {"type": "string"},
                "config": {"type": "string"},
            },
            "required": ["query", "response"],
        },
    )

    # Testing criteria: built-in evaluators
    testing_criteria = [
        {
            "type": "azure_ai_evaluator",
            "name": "coherence",
            "evaluator_name": "builtin.coherence",
            "initialization_parameters": {"deployment_name": judge_model},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "groundedness",
            "evaluator_name": "builtin.groundedness",
            "initialization_parameters": {"deployment_name": judge_model},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
                "context": "{{item.context}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "relevance",
            "evaluator_name": "builtin.relevance",
            "initialization_parameters": {"deployment_name": judge_model},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
    ]

    # Create the evaluation group (reusable across runs)
    eval_name = f"RAG Migration: {source_label} → {target_label}"
    print(f"\n  Creating evaluation: {eval_name}")
    eval_object = client.evals.create(
        name=eval_name,
        data_source_config=data_source_config,
        testing_criteria=testing_criteria,
    )
    print(f"  Eval ID: {eval_object.id}")

    # Create runs for each config
    for label, rows in [(source_label, source_rows), (target_label, target_rows)]:
        run_name = f"{label} | {time.strftime('%Y-%m-%d %H:%M')}"
        print(f"\n  Creating run: {run_name} ({len(rows)} items)")

        data_source = CreateEvalJSONLRunDataSourceParam(
            type="jsonl",
            source=SourceFileContent(
                type="file_content",
                content=[SourceFileContentContent(item=row) for row in rows],
            ),
        )

        eval_run = client.evals.runs.create(
            eval_id=eval_object.id,
            name=run_name,
            data_source=data_source,
        )
        print(f"  Run ID: {eval_run.id}")

        # Poll for completion
        while True:
            run = client.evals.runs.retrieve(
                run_id=eval_run.id, eval_id=eval_object.id
            )
            if run.status in ("completed", "failed"):
                break
            time.sleep(5)
            print(f"    Waiting... (status: {run.status})")

        if run.status == "failed":
            print(f"  ✗ Run failed: {run.error}")
        else:
            print(f"  ✓ Run completed")
            if hasattr(run, "report_url") and run.report_url:
                print(f"    Dashboard: {run.report_url}")

    print(f"\n  ✓ Both runs created under eval '{eval_name}'")
    print(f"    Compare them side-by-side in the Foundry portal.")


def main():
    parser = argparse.ArgumentParser(description="Upload eval results to Foundry")
    parser.add_argument(
        "--source-results", type=str, default=None,
        help="Path to source model DualLayerReport JSON",
    )
    parser.add_argument(
        "--target-results", type=str, default=None,
        help="Path to target model DualLayerReport JSON",
    )
    parser.add_argument(
        "--project-endpoint", type=str, default=None,
        help="Foundry project endpoint (or set AZURE_AI_PROJECT_ENDPOINT)",
    )
    parser.add_argument(
        "--judge-deployment", type=str, default=None,
        help="Model deployment for built-in evaluators (default: gpt-4o)",
    )
    args = parser.parse_args()

    results_dir = str(_REPO_ROOT / "samples" / "rag_pipeline" / "data" / "results")

    source_path = args.source_results or _find_latest_results(
        os.path.join(results_dir, "dual_layer_gpt-4o_*.json")
    )
    target_path = args.target_results or _find_latest_results(
        os.path.join(results_dir, "dual_layer_gpt-4.1_*.json")
    )

    if not source_path or not target_path:
        print("ERROR: No results files found. Run test_e2e.py first.")
        print(f"  Looked in: {results_dir}/")
        sys.exit(1)

    print("═" * 60)
    print("  Upload to Azure AI Foundry")
    print("═" * 60)

    upload_to_foundry(
        source_path=source_path,
        target_path=target_path,
        project_endpoint=args.project_endpoint,
        judge_deployment=args.judge_deployment,
    )


if __name__ == "__main__":
    main()
