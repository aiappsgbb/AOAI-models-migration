"""
Data Loader Utility
Loads synthetic evaluation data and test scenarios.

All dataclasses use **flat, CSV-compatible fields** — complex structures
(conversations, tool lists, …) are stored as text strings.  Expansion
methods (``get_*``) parse them back to rich Python objects when needed
by the evaluator or Foundry converter.

Legacy detection: if a JSON file still uses the old nested schema the
loader transparently normalises each item on-the-fly so no migration
step is strictly required.
"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Iterator
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════
# Parsing helpers (text → structured)
# ═══════════════════════════════════════════════════════════════════════

def _json_or_empty_dict(text: str) -> Dict[str, Any]:
    """Parse a JSON string to dict; return {} on failure."""
    if not text:
        return {}
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def _json_or_empty_list(text: str) -> list:
    """Parse a JSON string to list; return [] on failure."""
    if not text:
        return []
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return val
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _pipe_to_list(text: str) -> List[str]:
    """Split a pipe-separated string into a list of trimmed strings."""
    if not text:
        return []
    return [s.strip() for s in text.split("|") if s.strip()]


def _list_to_pipe(lst) -> str:
    """Convert a list of strings to a pipe-separated string."""
    if isinstance(lst, str):
        return lst
    if isinstance(lst, list):
        return " | ".join(str(v) for v in lst)
    return str(lst) if lst else ""


def _to_json_str(val) -> str:
    """Convert any value to a JSON string (passthrough if already str)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


# ═══════════════════════════════════════════════════════════════════════
# Dataclasses — flat, CSV-friendly schemas
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ClassificationScenario:
    """Classification test scenario — 7 flat fields."""
    id: str
    customer_input: str
    expected_category: str
    expected_subcategory: str = ""
    expected_priority: str = ""
    expected_sentiment: str = ""
    context: str = ""            # JSON string of a dict

    # ── Expansion ───────────────────────────────────────────────────
    def get_context_dict(self) -> Dict[str, Any]:
        """Parse *context* back to a Python dict."""
        return _json_or_empty_dict(self.context)


@dataclass
class DialogScenario:
    """Dialog / follow-up test scenario — 6 flat fields."""
    id: str
    conversation: str = ""       # JSON string of [{role, message}, …]
    context_gaps: str = ""       # pipe-separated
    optimal_follow_up: str = ""
    follow_up_rules: str = ""    # pipe-separated
    expected_resolution_turns: int = 2

    # ── Expansion ───────────────────────────────────────────────────
    def get_conversation_list(self) -> List[Dict[str, str]]:
        return _json_or_empty_list(self.conversation)

    def get_context_gaps_list(self) -> List[str]:
        return _pipe_to_list(self.context_gaps)

    def get_follow_up_rules_list(self) -> List[str]:
        return _pipe_to_list(self.follow_up_rules)


@dataclass
class GeneralTestCase:
    """General capability test case — 7 flat fields."""
    id: str
    test_type: str = ""
    prompt: str = ""
    complexity: str = "medium"
    expected_behavior: str = ""
    conversation: str = ""       # JSON string of [{role, content}, …]
    run_count: int = 1

    # ── Expansion ───────────────────────────────────────────────────
    def get_conversation_list(self) -> Optional[List[Dict]]:
        """Return parsed conversation or None if empty."""
        lst = _json_or_empty_list(self.conversation)
        return lst if lst else None


@dataclass
class RAGScenario:
    """RAG test scenario — 4 flat text fields (simplest type)."""
    id: str
    query: str = ""
    context: str = ""
    ground_truth: str = ""


@dataclass
class ToolCallingScenario:
    """Tool-calling test scenario — 5 flat fields."""
    id: str
    query: str = ""
    available_tools: str = ""       # JSON string of tool defs
    expected_tool_calls: str = ""   # pipe-separated function names
    expected_parameters: str = ""   # JSON string of expected params

    # ── Expansion ───────────────────────────────────────────────────
    def get_tools_list(self) -> List[Dict[str, Any]]:
        return _json_or_empty_list(self.available_tools)

    def get_expected_calls_list(self) -> List[str]:
        return _pipe_to_list(self.expected_tool_calls)

    def get_expected_params_dict(self) -> Dict[str, Any]:
        return _json_or_empty_dict(self.expected_parameters)


# ═══════════════════════════════════════════════════════════════════════
# Legacy normalisation — old nested schema → new flat schema
# ═══════════════════════════════════════════════════════════════════════

def _normalise_classification(item: dict) -> dict:
    ctx = item.get("context", {})
    return {
        "id": item.get("id", ""),
        "customer_input": item.get("customer_input", ""),
        "expected_category": item.get("expected_category", ""),
        "expected_subcategory": item.get("expected_subcategory", ""),
        "expected_priority": item.get("expected_priority", ""),
        "expected_sentiment": item.get("expected_sentiment", ""),
        "context": _to_json_str(ctx) if not isinstance(ctx, str) else ctx,
    }


def _normalise_dialog(item: dict) -> dict:
    conv = item.get("conversation", [])
    return {
        "id": item.get("id", ""),
        "conversation": _to_json_str(conv) if not isinstance(conv, str) else conv,
        "context_gaps": _list_to_pipe(item.get("context_gaps", [])),
        "optimal_follow_up": item.get("optimal_follow_up", ""),
        "follow_up_rules": _list_to_pipe(item.get("follow_up_rules", [])),
        "expected_resolution_turns": item.get("expected_resolution_turns", 2),
    }


def _normalise_general(item: dict) -> dict:
    conv = item.get("conversation")
    return {
        "id": item.get("id", ""),
        "test_type": item.get("test_type", ""),
        "prompt": item.get("prompt", ""),
        "complexity": item.get("complexity", "medium"),
        "expected_behavior": item.get("expected_behavior", ""),
        "conversation": _to_json_str(conv) if conv and not isinstance(conv, str) else (conv or ""),
        "run_count": item.get("run_count", 1),
    }


def _normalise_rag(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "query": item.get("query", ""),
        "context": item.get("context", ""),
        "ground_truth": item.get("ground_truth", ""),
    }


def _normalise_tool_calling(item: dict) -> dict:
    tools = item.get("available_tools", [])
    calls = item.get("expected_tool_calls", [])
    params = item.get("expected_parameters", {})
    return {
        "id": item.get("id", ""),
        "query": item.get("query", ""),
        "available_tools": _to_json_str(tools) if not isinstance(tools, str) else tools,
        "expected_tool_calls": _list_to_pipe(calls) if not isinstance(calls, str) else calls,
        "expected_parameters": _to_json_str(params) if not isinstance(params, str) else params,
    }


def _needs_normalisation(item: dict, task: str) -> bool:
    """Return True when *item* still uses the old nested schema."""
    if task == "classification":
        return (isinstance(item.get("context"), dict)
                or "scenario" in item
                or "follow_up_questions_expected" in item)
    if task == "dialog":
        return (isinstance(item.get("conversation"), list)
                or "scenario" in item
                or "category" in item)
    if task == "general":
        return (isinstance(item.get("conversation"), list)
                or "expected_output" in item)
    if task == "rag":
        return ("scenario" in item
                or "expected_behavior" in item
                or "complexity" in item)
    if task == "tool_calling":
        return (isinstance(item.get("available_tools"), list)
                or "scenario" in item
                or "complexity" in item)
    return False


_NORMALISERS = {
    "classification": _normalise_classification,
    "dialog": _normalise_dialog,
    "general": _normalise_general,
    "rag": _normalise_rag,
    "tool_calling": _normalise_tool_calling,
}


def ensure_flat_schema(items: list, task: str) -> list:
    """Normalise *items* to the canonical flat schema.

    Safe to call on data that is already flat — items that do not need
    normalisation are returned unchanged.  Unknown *task* names return
    items as-is.
    """
    normaliser = _NORMALISERS.get(task)
    if not normaliser:
        return items
    return [
        normaliser(it) if _needs_normalisation(it, task) else it
        for it in items
    ]


# ═══════════════════════════════════════════════════════════════════════
# DataLoader
# ═══════════════════════════════════════════════════════════════════════

class DataLoader:
    """
    Utility class for loading evaluation data.
    Provides structured access to classification, dialog, general, RAG,
    and tool-calling test scenarios.

    Supports both **JSON** and **CSV** source files, and transparently
    normalises legacy nested schemas to the new flat format.
    """

    def __init__(self, data_dir: str = "data/synthetic"):
        self.data_dir = Path(data_dir)
        self._cache: Dict[str, Any] = {}

    def clear_cache(self):
        """Clear the data cache so fresh files are loaded on next access."""
        self._cache.clear()

    # ── Low-level loaders ─────────────────────────────────────────────

    def _load_json(self, file_path: Path) -> Any:
        """Load and cache a JSON file."""
        key = str(file_path)
        if key not in self._cache:
            with open(file_path, "r", encoding="utf-8") as f:
                self._cache[key] = json.load(f)
        return self._cache[key]

    def _load_csv(self, file_path: Path) -> List[dict]:
        """Load a CSV file into a list of dicts (DictReader)."""
        key = str(file_path)
        if key not in self._cache:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                self._cache[key] = list(csv.DictReader(f))
        return self._cache[key]

    def _detect_and_load(self, json_path: Path) -> List[dict]:
        """Load from JSON or CSV — CSV is tried when the JSON file is missing."""
        if json_path.exists():
            return self._load_json(json_path)
        csv_path = json_path.with_suffix(".csv")
        if csv_path.exists():
            return self._load_csv(csv_path)
        raise FileNotFoundError(f"Neither {json_path} nor {csv_path} exists")

    def _load_and_normalise(self, json_path: Path, task: str) -> List[dict]:
        """Load raw items and normalise legacy schemas on-the-fly."""
        raw = self._detect_and_load(json_path)
        normaliser = _NORMALISERS.get(task)
        if not normaliser:
            return raw
        result = []
        for item in raw:
            if _needs_normalisation(item, task):
                result.append(normaliser(item))
            else:
                result.append(item)
        return result

    # ── Public loaders ────────────────────────────────────────────────

    def load_classification_scenarios(self) -> List[ClassificationScenario]:
        file_path = self.data_dir / "classification" / "classification_scenarios.json"
        items = self._load_and_normalise(file_path, "classification")
        return [
            ClassificationScenario(
                id=item.get("id", ""),
                customer_input=item.get("customer_input", ""),
                expected_category=item.get("expected_category", ""),
                expected_subcategory=item.get("expected_subcategory", ""),
                expected_priority=item.get("expected_priority", ""),
                expected_sentiment=item.get("expected_sentiment", ""),
                context=item.get("context", ""),
            )
            for item in items
        ]

    def load_dialog_scenarios(self) -> List[DialogScenario]:
        file_path = self.data_dir / "dialog" / "follow_up_scenarios.json"
        items = self._load_and_normalise(file_path, "dialog")
        return [
            DialogScenario(
                id=item.get("id", ""),
                conversation=item.get("conversation", ""),
                context_gaps=item.get("context_gaps", ""),
                optimal_follow_up=item.get("optimal_follow_up", ""),
                follow_up_rules=item.get("follow_up_rules", ""),
                expected_resolution_turns=int(item.get("expected_resolution_turns") or 2),
            )
            for item in items
        ]

    def load_general_tests(self) -> List[GeneralTestCase]:
        file_path = self.data_dir / "general" / "capability_tests.json"
        items = self._load_and_normalise(file_path, "general")
        return [
            GeneralTestCase(
                id=item.get("id", ""),
                test_type=item.get("test_type", ""),
                prompt=item.get("prompt", ""),
                complexity=item.get("complexity", "medium"),
                expected_behavior=item.get("expected_behavior", ""),
                conversation=item.get("conversation", ""),
                run_count=int(item.get("run_count") or 1),
            )
            for item in items
        ]

    def load_rag_scenarios(self) -> List[RAGScenario]:
        file_path = self.data_dir / "rag" / "rag_scenarios.json"
        items = self._load_and_normalise(file_path, "rag")
        return [
            RAGScenario(
                id=item.get("id", ""),
                query=item.get("query", ""),
                context=item.get("context", ""),
                ground_truth=item.get("ground_truth", ""),
            )
            for item in items
        ]

    def load_tool_calling_scenarios(self) -> List[ToolCallingScenario]:
        file_path = self.data_dir / "tool_calling" / "tool_calling_scenarios.json"
        items = self._load_and_normalise(file_path, "tool_calling")
        return [
            ToolCallingScenario(
                id=item.get("id", ""),
                query=item.get("query", ""),
                available_tools=item.get("available_tools", ""),
                expected_tool_calls=item.get("expected_tool_calls", ""),
                expected_parameters=item.get("expected_parameters", ""),
            )
            for item in items
        ]

    # ── Filters ───────────────────────────────────────────────────────

    def get_classification_by_category(self, category: str) -> List[ClassificationScenario]:
        return [s for s in self.load_classification_scenarios()
                if s.expected_category == category]

    def get_classification_by_priority(self, priority: str) -> List[ClassificationScenario]:
        return [s for s in self.load_classification_scenarios()
                if s.expected_priority == priority]

    def get_tests_by_type(self, test_type: str) -> List[GeneralTestCase]:
        return [t for t in self.load_general_tests()
                if t.test_type == test_type]

    def get_tests_by_complexity(self, complexity: str) -> List[GeneralTestCase]:
        return [t for t in self.load_general_tests()
                if t.complexity == complexity]

    # ── Iteration ─────────────────────────────────────────────────────

    def iter_all_scenarios(self) -> Iterator[tuple]:
        for scenario in self.load_classification_scenarios():
            yield ("classification", scenario)
        for scenario in self.load_dialog_scenarios():
            yield ("dialog", scenario)
        for test in self.load_general_tests():
            yield ("general", test)
        for scenario in self.load_rag_scenarios():
            yield ("rag", scenario)
        for scenario in self.load_tool_calling_scenarios():
            yield ("tool_calling", scenario)

    # ── Summary ───────────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        classification = self.load_classification_scenarios()
        dialog = self.load_dialog_scenarios()
        general = self.load_general_tests()

        try:
            rag = self.load_rag_scenarios()
        except (FileNotFoundError, KeyError):
            rag = []
        try:
            tool_calling = self.load_tool_calling_scenarios()
        except (FileNotFoundError, KeyError):
            tool_calling = []

        total = len(classification) + len(dialog) + len(general) + len(rag) + len(tool_calling)

        summary: Dict[str, Any] = {
            "total_scenarios": total,
            "classification": {
                "count": len(classification),
                "categories": list(set(s.expected_category for s in classification)),
                "priorities": list(set(s.expected_priority for s in classification)),
            },
            "dialog": {
                "count": len(dialog),
            },
            "general": {
                "count": len(general),
                "test_types": list(set(t.test_type for t in general)),
                "complexity_levels": list(set(t.complexity for t in general)),
            },
        }
        if rag:
            summary["rag"] = {"count": len(rag)}
        if tool_calling:
            summary["tool_calling"] = {"count": len(tool_calling)}
        return summary


# Example usage
if __name__ == "__main__":
    loader = DataLoader("data/synthetic")

    print("Data Loader Utility")
    print("=" * 50)

    summary = loader.get_summary()
    print(f"\nTotal scenarios: {summary['total_scenarios']}")
    print(f"\nClassification: {summary['classification']['count']} scenarios")
    print(f"  Categories: {summary['classification']['categories']}")
    print(f"\nDialog: {summary['dialog']['count']} scenarios")
    print(f"\nGeneral: {summary['general']['count']} tests")
    print(f"  Test types: {summary['general']['test_types']}")
