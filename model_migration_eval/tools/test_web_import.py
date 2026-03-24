#!/usr/bin/env python
"""
Test script: Import telco_customer_service topic via the web API
(mimics what the Prompts page does) and generate prompts for all
target models under a new topic "test_import_telco_customer_service".

Uses the real Azure OpenAI client (requires valid credentials in .env / environment).

Usage:
    python tools/test_web_import.py
"""
import json
import os
import sys
import io
import time

# Ensure project root is in path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pathlib import Path


def main():
    print("=" * 60, flush=True)
    print("TEST WEB IMPORT: telco_customer_service → test_import_telco_customer_service", flush=True)
    print("=" * 60, flush=True)

    # Disable code verification so we can authenticate with just an email
    os.environ['AUTH_CODE_VERIFICATION'] = 'false'

    # ── Import Flask app (real settings, real Azure connection) ──
    print("\n[1/5] Creating Flask app...", flush=True)
    from src.web.routes import create_app
    app = create_app()
    app.config['TESTING'] = True

    prompts_dir = Path(ROOT) / "prompts" / "topics" / "telco_customer_service" / "gpt4o"
    data_dir = Path(ROOT) / "data" / "synthetic" / "topics" / "telco_customer_service"

    # ── Prepare source prompt files ──
    print("\n[2/5] Preparing source files...", flush=True)

    prompt_map = {
        "source_class_prompt":  prompts_dir / "classification_agent_system.md",
        "source_dialog_prompt": prompts_dir / "dialog_agent_system.md",
        "source_rag_prompt":    prompts_dir / "rag_agent_system.md",
        "source_tool_prompt":   prompts_dir / "tool_calling_agent_system.md",
    }

    data_map = {
        "class_test_data":        data_dir / "classification" / "classification_scenarios.json",
        "dialog_test_data":       data_dir / "dialog" / "follow_up_scenarios.json",
        "general_test_data":      data_dir / "general" / "capability_tests.json",
        "rag_test_data":          data_dir / "rag" / "rag_scenarios.json",
        "tool_calling_test_data": data_dir / "tool_calling" / "tool_calling_scenarios.json",
    }

    # Show what we're sending
    for field, path in {**prompt_map, **data_map}.items():
        status = f"✓ {path.stat().st_size:,} bytes" if path.exists() else "✗ NOT FOUND"
        print(f"  {field}: {path.name} ({status})", flush=True)

    # ── Build multipart form data ──
    # Flask test client expects: data={field: (BytesIO, filename, mimetype), ...}
    form_data: dict = {
        "topic": "test_import_telco_customer_service",
        "source_model": "gpt4o",
        "generator_model": "gpt5",
        "force": "true",
    }

    for field, path in prompt_map.items():
        if path.exists():
            form_data[field] = (io.BytesIO(path.read_bytes()), path.name, "text/markdown")

    for field, path in data_map.items():
        if path.exists():
            form_data[field] = (io.BytesIO(path.read_bytes()), path.name, "application/json")

    # ── Authenticate + call import ──
    print("\n[3/5] Authenticating...", flush=True)

    with app.test_client() as client:
        # Authenticate first (code_verification=False → immediate auth)
        auth_resp = client.post("/api/auth/login", json={"email": "test@example.com"})
        auth_data = auth_resp.get_json()
        if auth_resp.status_code != 200 or auth_data.get("status") != "authenticated":
            print(f"  ✗ Authentication failed: {auth_data}", flush=True)
            return 1
        print(f"  ✓ Authenticated as {auth_data.get('email', 'test@example.com')}", flush=True)
        print(f"    user_id: {auth_data.get('user_id', '?')}", flush=True)

        print(f"\n[4/5] Sending import request to /api/topics/import …", flush=True)
        print(f"  topic:           {form_data['topic']}", flush=True)
        print(f"  source_model:    {form_data['source_model']}", flush=True)
        print(f"  generator_model: {form_data['generator_model']}", flush=True)
        print(f"  force:           {form_data['force']}", flush=True)
        print(f"\n  ⏳ Generating prompts for all target models (this may take 2-5 min)...", flush=True)

        t0 = time.time()
        resp = client.post(
            "/api/topics/import",
            data=form_data,
            content_type="multipart/form-data",
        )
        elapsed = time.time() - t0

    # ── Show results ──
    print(f"\n{'=' * 60}", flush=True)
    print(f"[5/5] Response", flush=True)
    print(f"  Status:  {resp.status_code}", flush=True)
    print(f"  Elapsed: {elapsed:.1f}s", flush=True)
    print(f"{'=' * 60}", flush=True)

    result = resp.get_json()
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    else:
        print(resp.data.decode(), flush=True)

    if resp.status_code == 200:
        print(f"\n✅ Import completed successfully!", flush=True)

        # Show generated files
        slug = result.get("slug", "test_import_telco_customer_service")
        user_id = auth_data.get("user_id", "test_at_example_com")

        for label, base in [
            ("Prompts", Path(ROOT) / "data" / "users" / user_id / "prompts" / "topics" / slug),
            ("Data",    Path(ROOT) / "data" / "users" / user_id / "synthetic" / "topics" / slug),
        ]:
            if base.exists():
                print(f"\n📁 {label}: {base}", flush=True)
                for item in sorted(base.rglob("*")):
                    if item.is_file():
                        rel = item.relative_to(base)
                        print(f"   {rel} ({item.stat().st_size:,} bytes)", flush=True)
            else:
                print(f"\n📁 {label}: {base} — NOT FOUND", flush=True)

        return 0
    else:
        print(f"\n❌ Import failed with status {resp.status_code}", flush=True)
        return 1


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
