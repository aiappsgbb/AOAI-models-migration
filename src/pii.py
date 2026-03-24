"""
PII redaction utilities for golden dataset preparation.

Uses Azure AI Language PII detection to redact personally identifiable
information from production data before evaluation. This is optional —
only needed when golden datasets contain real customer data.

Requires: pip install azure-ai-textanalytics azure-identity
"""

import os
from typing import Optional


def create_pii_client(
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
):
    """
    Create an Azure AI Language client for PII detection.

    Args:
        endpoint: Azure AI Language endpoint. Falls back to AZURE_LANGUAGE_ENDPOINT env var.
        key: API key. Falls back to AZURE_LANGUAGE_KEY env var. If neither is set,
             uses DefaultAzureCredential (Entra ID).

    Returns:
        TextAnalyticsClient instance.
    """
    from azure.ai.textanalytics import TextAnalyticsClient

    endpoint = endpoint or os.getenv("AZURE_LANGUAGE_ENDPOINT")
    if not endpoint:
        raise ValueError(
            "Azure AI Language endpoint required. Set AZURE_LANGUAGE_ENDPOINT "
            "env var or pass endpoint= parameter."
        )

    key = key or os.getenv("AZURE_LANGUAGE_KEY")
    if key:
        from azure.core.credentials import AzureKeyCredential
        return TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    else:
        from azure.identity import DefaultAzureCredential
        return TextAnalyticsClient(endpoint=endpoint, credential=DefaultAzureCredential())


def redact_text(
    client,
    text: str,
    categories: Optional[list[str]] = None,
    language: str = "en",
) -> str:
    """
    Redact PII entities from a single text string.

    Args:
        client: TextAnalyticsClient from create_pii_client().
        text: Text to redact.
        categories: PII categories to redact (default: all). Examples:
            "Person", "Email", "PhoneNumber", "Address", "Organization",
            "CreditCardNumber", "IPAddress", "URL", "DateTime".
        language: Text language (default: "en"). Use "it" for Italian, etc.

    Returns:
        Redacted text with PII replaced by category placeholders like [PERSON], [EMAIL].
    """
    from azure.ai.textanalytics import PiiEntityCategory

    kwargs = {"language": language}
    if categories:
        kwargs["categories_filter"] = [
            PiiEntityCategory[cat] if isinstance(cat, str) else cat
            for cat in categories
        ]

    results = client.recognize_pii_entities([text], **kwargs)
    result = results[0]

    if result.is_error:
        raise RuntimeError(f"PII detection failed: {result.error.message}")

    return result.redacted_text


def redact_test_cases(
    test_cases,
    client=None,
    fields: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    language: str = "en",
):
    """
    Redact PII from a list of TestCase objects or dicts.

    Args:
        test_cases: list[TestCase] or list[dict] — loaded from JSONL.
        client: TextAnalyticsClient. If None, creates one from env vars.
        fields: Which fields to redact (default: ["prompt", "expected_output", "context"]).
        categories: PII categories to redact (default: all).
        language: Text language (default: "en").

    Returns:
        New list with PII redacted. Original objects are NOT modified.

    Example:
        from src.pii import redact_test_cases
        from src.evaluate.core import load_test_cases

        cases = load_test_cases("data/production_export.jsonl")
        clean = redact_test_cases(cases, language="it")  # Italian PII detection
        save_test_cases(clean, "data/production_export_clean.jsonl")
    """
    import copy
    from dataclasses import asdict, fields as dc_fields

    if client is None:
        client = create_pii_client()

    fields_to_redact = fields or ["prompt", "expected_output", "context", "system_prompt"]
    redacted = []

    for tc in test_cases:
        # Handle both TestCase objects and dicts
        if hasattr(tc, "__dataclass_fields__"):
            item = copy.deepcopy(tc)
            for field in fields_to_redact:
                val = getattr(item, field, None)
                if val and isinstance(val, str) and val.strip():
                    setattr(item, field, redact_text(client, val, categories, language))
        else:
            item = copy.deepcopy(tc)
            for field in fields_to_redact:
                val = item.get(field)
                if val and isinstance(val, str) and val.strip():
                    item[field] = redact_text(client, val, categories, language)

        redacted.append(item)

    return redacted


def redact_jsonl_file(
    input_path: str,
    output_path: str,
    categories: Optional[list[str]] = None,
    language: str = "en",
    fields: Optional[list[str]] = None,
):
    """
    Redact PII from a JSONL file and write to a new file.

    Args:
        input_path: Path to input JSONL file.
        output_path: Path to write redacted JSONL file.
        categories: PII categories to redact (default: all).
        language: Text language.
        fields: Fields to redact (default: prompt, expected_output, context, system_prompt).

    Example:
        from src.pii import redact_jsonl_file
        redact_jsonl_file(
            "data/production_export.jsonl",
            "data/production_export_clean.jsonl",
            language="it",
        )
    """
    import json

    client = create_pii_client()
    fields_to_redact = fields or ["prompt", "expected_output", "context", "system_prompt"]

    count = 0
    with open(input_path, "r", encoding="utf-8") as fin, \
         open(output_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for field in fields_to_redact:
                val = record.get(field)
                if val and isinstance(val, str) and val.strip():
                    record[field] = redact_text(client, val, categories, language)
            json.dump(record, fout, ensure_ascii=False)
            fout.write("\n")
            count += 1

    print(f"✅ Redacted {count} records → {output_path}")
