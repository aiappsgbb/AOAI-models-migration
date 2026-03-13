"""Fix AI topic classification data to align subcategories and sentiments with prompt taxonomy."""
import json, shutil
from pathlib import Path

data_path = Path("data/synthetic/topics/ai_agent_to_answer_questions_about_ai/classification/classification_scenarios.json")

# Backup first
shutil.copy2(data_path, data_path.with_suffix(".bak_manual_fix.json"))

with open(data_path, "r", encoding="utf-8") as f:
    scenarios = json.load(f)

# Subcategory mapping: data value -> (correct_category, correct_subcategory)
subcat_map = {
    "foundational_explanations":             ("ai_concepts_and_learning",         "definitions_and_intuition"),
    "terminology_clarification":             ("ai_concepts_and_learning",         "limitations_and_failure_modes"),
    "model_tradeoff_analysis":               ("model_selection_and_architecture", "model_comparison_and_tradeoffs"),
    "multi_model_routing":                   ("model_selection_and_architecture", "architecture_design_patterns"),
    "structured_response_templates":         ("prompt_engineering_and_interaction","structured_outputs_and_json"),
    "conversation_memory_management":        ("model_selection_and_architecture", "context_window_and_memory_strategy"),
    "dataset_schema_and_labeling":           ("data_and_datasets",               "data_collection_and_labeling"),
    "data_quality_and_sanitization":         ("data_and_datasets",               "data_quality_and_cleaning"),
    "supervised_fine_tuning_strategy":       ("training_and_fine_tuning",         "fine_tuning_methods"),
    "preference_tuning_and_alignment":       ("training_and_fine_tuning",         "alignment_and_preference_optimization"),
    "offline_and_online_metrics":            ("evaluation_and_metrics",           "offline_evaluation_design"),
    "hallucination_and_grounding_tests":     ("evaluation_and_metrics",           "llm_specific_metrics"),
    "release_management_and_versioning":     ("deployment_and_mlop",              "ci_cd_and_release_management"),
    "performance_and_scaling":               ("deployment_and_mlop",              "serving_and_scaling"),
    "chunking_and_index_design":             ("retrieval_and_rag",               "chunking_and_document_processing"),
    "reranking_and_query_rewriting":         ("retrieval_and_rag",               "reranking_and_hybrid_search"),
    "tool_calling_and_orchestration":        ("agents_and_tooling",              "tool_use_and_function_calling"),
    "planning_and_budgeting_controls":       ("agents_and_tooling",              "planning_and_reasoning_patterns"),
    "prompt_injection_and_data_exfiltration": ("security_privacy_and_compliance", "threat_modeling_and_attack_surface"),
    "dual_use_request_handling":             ("safety_policy_and_ethics",         "harmful_content_and_misuse_prevention"),
}

# Sentiment mapping: descriptive emotions -> polarity scale
sentiment_map = {
    "professional": "neutral",
    "curious":      "neutral",
    "cautious":     "neutral",
    "frustrated":   "negative",
    "worried":      "negative",
    "concerned":    "negative",
    "angry":        "very_negative",
    "very_angry":   "very_negative",
}

changes = []
for s in scenarios:
    old_sub = s["expected_subcategory"]
    old_sent = s["expected_sentiment"]
    old_cat = s["expected_category"]

    if old_sub in subcat_map:
        new_cat, new_sub = subcat_map[old_sub]
        if old_cat != new_cat:
            changes.append(f"{s['id']}: category {old_cat} -> {new_cat}")
            s["expected_category"] = new_cat
        changes.append(f"{s['id']}: subcategory {old_sub} -> {new_sub}")
        s["expected_subcategory"] = new_sub

    if old_sent in sentiment_map:
        new_sent = sentiment_map[old_sent]
        changes.append(f"{s['id']}: sentiment {old_sent} -> {new_sent}")
        s["expected_sentiment"] = new_sent

with open(data_path, "w", encoding="utf-8") as f:
    json.dump(scenarios, f, indent=2, ensure_ascii=False)

print(f"Applied {len(changes)} changes:")
for c in changes:
    print(f"  {c}")

# Verify
cats = set(s["expected_category"] for s in scenarios)
subs = set(s["expected_subcategory"] for s in scenarios)
pris = set(s["expected_priority"] for s in scenarios)
sents = set(s["expected_sentiment"] for s in scenarios)
print(f"\nCategories ({len(cats)}): {sorted(cats)}")
print(f"Subcategories ({len(subs)}): {sorted(subs)}")
print(f"Priorities ({len(pris)}): {sorted(pris)}")
print(f"Sentiments ({len(sents)}): {sorted(sents)}")
