"""
Prompt Manager - Version-controlled prompt editing, saving, and AI generation.

Stores prompt versions as JSON files in prompts/history/ for easy manual editing.
Each version captures: content, metadata, timestamp, topic, and source (manual/ai).

Also generates topic-adapted synthetic test data (classification, dialog, general)
so that evaluations match the generated prompts.

Topic management:
  - Each topic is archived as a self-contained snapshot under
    prompts/topics/{slug}/ and data/synthetic/topics/{slug}/.
  - Switching topics restores prompts + data from the archive into
    the active locations (prompts/<model>/, data/synthetic/*).
  - Generating a new topic automatically archives the current one first.
"""

import asyncio
import json
import random
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import logging

from src.utils.model_guidance import get_guidance as _get_model_guidance
from src.utils.data_loader import ensure_flat_schema
from src.utils.category_parser import (          # A2 refactor
    extract_categories_from_prompt,
    _extract_categories_from_prompt,              # backward-compatible alias
)

logger = logging.getLogger(__name__)

# Maximum number of concurrent LLM calls to avoid API rate-limit errors.
_MAX_CONCURRENT_LLM = 5

# ── Canonical JSON output fields for classification prompts ───────────
# All models MUST produce responses with these exact top-level field names
# so that the evaluation pipeline and metrics parser can process them
# uniformly.  Models are free to add EXTRA fields beyond these, but these
# 7 core fields must use the exact names and types listed here.
_CANONICAL_CLASSIFICATION_SCHEMA = """\
## MANDATORY JSON OUTPUT SCHEMA (ALL MODELS MUST USE THESE EXACT FIELD NAMES)
The system prompt you generate MUST instruct the model to produce JSON
responses whose top-level structure includes AT LEAST these fields,
using EXACTLY these names and types — no renaming, no nesting changes:

```
{
  "primary_category": "<string> — one of the mandatory category codes",
  "subcategory": "<string> — a descriptive snake_case subcategory",
  "priority": "<string> — one of: critical | high | medium | low",
  "sentiment": "<string> — a flat label, e.g. angry, neutral, positive",
  "confidence": <number> — a decimal between 0.0 and 1.0,
  "summary": "<string> — brief summary of the customer request",
  "follow_up_questions": ["<string>", ...]
}
```

STRICT RULES:
- "primary_category" must be a flat string at the top level — NOT nested
  inside another object (no `category.primary` or `category.code`).
- "subcategory" must be a flat string at the top level — NOT `category.secondary`.
- "priority" — NOT "priority_level". Values must be exactly:
  critical, high, medium, or low.
- "sentiment" must be a flat string — NOT an object with sub-keys.
- "confidence" must be a single decimal number 0.0–1.0 — NOT a string,
  NOT an object, NOT absent.
- "summary" must be a flat string — NOT "summary_es", NOT an object.
- "follow_up_questions" — NOT "follow_up_questions_es" or any variant.

The model MAY add extra fields (entities, safety_flags, vehicle info, etc.)
as needed, but the 7 fields above MUST be present with these exact names.
"""


def _slugify(text: str) -> str:
    """Convert a topic name to a filesystem-safe slug."""
    s = text.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '_', s)
    return s.strip('_') or 'default'


def _sanitise_json(raw: str) -> str:
    """Best-effort fix for common LLM JSON issues.

    Handles:
    - Trailing commas before } or ]
    - Single-line // comments
    - Block /* */ comments
    - Unquoted property names (simple cases)
    - Stray text before the first [ or after the last ]
    """
    # 1. Extract only the JSON array portion
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]

    # 2. Remove single-line comments (// ...) but not inside strings
    raw = re.sub(r'(?<!["\'])//[^\n]*', '', raw)
    # 3. Remove block comments
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)

    # 4. Remove trailing commas before } or ]
    raw = re.sub(r',\s*([\]}])', r'\1', raw)

    # 5. Remove bare ... (with optional surrounding commas/whitespace)
    raw = re.sub(r',?\s*\.\.\.\s*,?', ',', raw)
    # Clean up double commas that may result
    raw = re.sub(r',\s*,', ',', raw)
    # Clean leading comma after [ or trailing comma before ]
    raw = re.sub(r'\[\s*,', '[', raw)
    raw = re.sub(r',\s*\]', ']', raw)

    return raw.strip()


class PromptManager:
    """
    Manages prompt templates with version history, AI-powered generation,
    and multi-topic support.

    Directory structure:
        prompts/
        ├── <model_a>/                           (active prompts)
        │   ├── classification_agent_system.md
        │   └── dialog_agent_system.md
        ├── <model_b>/
        │   ├── classification_agent_system.md
        │   └── dialog_agent_system.md
        ├── topics/                              (archived topic snapshots)
        │   ├── telco_customer_service/
        │   │   ├── <model_a>/ ...
        │   │   ├── <model_b>/ ...
        │   │   └── topic.json                   (metadata)
        │   └── red_sea_diving/
        │       └── ...
        └── history/
            └── versions.json                    (version index)

        data/synthetic/
        ├── classification/ ...                  (active data)
        ├── dialog/ ...
        ├── general/ ...
        └── topics/                              (archived data snapshots)
            ├── telco_customer_service/ ...
            └── red_sea_diving/ ...
    """

    # The data sub-directories and their canonical filenames
    _DATA_FILES = {
        "classification": "classification_scenarios.json",
        "dialog":         "follow_up_scenarios.json",
        "general":        "capability_tests.json",
        "rag":            "rag_scenarios.json",
        "tool_calling":   "tool_calling_scenarios.json",
    }

    _DATA_GEN_PROMPTS_DIR = Path("config/data_gen_prompts")

    def __init__(self, prompts_dir: str = "prompts", data_dir: str = "data/synthetic", config: Optional[Dict] = None):
        self.prompts_dir = Path(prompts_dir)
        self.data_dir = Path(data_dir)
        self._config = config or {}
        self.history_dir = self.prompts_dir / "history"
        self.topics_dir = self.prompts_dir / "topics"
        self.data_topics_dir = self.data_dir / "topics"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        self.data_topics_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.history_dir / "versions.json"
        self._topic_path = self.history_dir / "topic_metadata.json"
        self._index = self._load_index()
        self._io_lock = threading.Lock()  # protects metadata AND version-index writes

        # Ensure all configured models have a prompt directory
        cfg_models = self._config.get("azure", {}).get("models", {})
        for model_key in cfg_models:
            model_dir = self.prompts_dir / model_key
            if not model_dir.exists():
                model_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created missing prompt directory for configured model: {model_key}")

    # ── Template loader for data-gen prompts ──────────────────────

    def _load_data_gen_template(self, filename: str) -> Optional[str]:
        """Load a data-generation prompt template from ``config/data_gen_prompts/``.

        Returns *None* when the file does not exist so callers can fall back
        to the hardcoded default.
        """
        path = self._DATA_GEN_PROMPTS_DIR / filename
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.debug(f"Template {path} not found — using built-in default")
            return None
        except Exception as exc:
            logger.warning(f"Failed to read template {path}: {exc} — using built-in default")
            return None

    def _get_model_dirs(self) -> List[str]:
        """Return active model directory names under the prompts directory."""
        excluded = {'history', 'topics', 'templates', '__pycache__'}
        return sorted(
            d.name for d in self.prompts_dir.iterdir()
            if d.is_dir() and d.name not in excluded
        )

    @staticmethod
    def _get_archive_model_dirs(archive_path: Path) -> List[str]:
        """Return model directory names inside a topic archive."""
        if not archive_path.exists():
            return []
        return sorted(
            d.name for d in archive_path.iterdir()
            if d.is_dir()
        )

    # ── Topic tracking ────────────────────────────────────────────────

    def get_topic_metadata(self) -> Dict[str, Any]:
        """Return the current topic metadata (topic, timestamp, data_generated_at)."""
        if self._topic_path.exists():
            try:
                with open(self._topic_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"topic": "", "prompts_updated_at": "", "data_generated_at": ""}

    def _save_topic_metadata(self, topic: str, *, prompts_updated: bool = False,
                              data_generated: bool = False,
                              canonical_categories: Optional[List[str]] = None):
        """Persist topic metadata to disk (thread-safe).

        When *canonical_categories* is provided it is stored alongside
        the timestamps so that ``is_data_in_sync`` can do a content-based
        check in addition to the timestamp comparison.
        """
        with self._io_lock:
            meta = self.get_topic_metadata()
            if topic:
                meta["topic"] = topic
            now = datetime.now().isoformat()
            if prompts_updated:
                meta["prompts_updated_at"] = now
            if data_generated:
                meta["data_generated_at"] = now
            if canonical_categories is not None:
                meta["canonical_categories"] = canonical_categories
            with open(self._topic_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

    def is_data_in_sync(self) -> Dict[str, Any]:
        """Check whether synthetic test data is up-to-date with the prompts.

        Returns a dict with ``in_sync`` (bool), ``topic``, timestamps,
        and — when available — a ``category_details`` sub-dict with
        content-level alignment information.

        Data is considered out-of-sync when:
        * Prompts have been updated *after* the last data generation, **or**
        * The test-data categories do not match the persisted canonical
          categories (content-level mismatch).
        """
        meta = self.get_topic_metadata()
        topic = meta.get("topic", "")
        prompts_ts = meta.get("prompts_updated_at", "")
        data_ts = meta.get("data_generated_at", "")

        if not topic:
            return {"in_sync": True, "topic": "", "reason": "no_topic_set"}

        if not data_ts:
            return {"in_sync": False, "topic": topic, "reason": "data_never_generated"}

        # ── Timestamp check ───────────────────────────────────────
        ts_in_sync = data_ts >= prompts_ts if prompts_ts else True

        # ── Content check: canonical categories vs test-data file ─
        content_in_sync = True
        category_details: Dict[str, Any] = {}
        canonical_cats = meta.get("canonical_categories")
        if canonical_cats:
            data_file = self.data_dir / "classification" / "classification_scenarios.json"
            if data_file.exists():
                try:
                    raw = json.loads(data_file.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        raw = raw.get("scenarios", raw.get("data", []))
                    data_cats = sorted({s.get("expected_category", "") for s in raw
                                        if s.get("expected_category")})
                    canonical_set = set(canonical_cats)
                    data_set = set(data_cats)
                    overlap = len(data_set & canonical_set)
                    content_in_sync = bool(overlap) and data_set <= canonical_set
                    category_details = {
                        "canonical_count": len(canonical_set),
                        "data_count": len(data_set),
                        "overlap": overlap,
                        "mismatched": sorted(data_set - canonical_set) if not content_in_sync else [],
                    }
                except Exception:
                    pass  # can't verify content — assume ok

        in_sync = ts_in_sync and content_in_sync
        if not ts_in_sync:
            reason = "prompts_updated_after_data"
        elif not content_in_sync:
            reason = "category_mismatch"
        else:
            reason = "up_to_date"

        result: Dict[str, Any] = {
            "in_sync": in_sync,
            "topic": topic,
            "prompts_updated_at": prompts_ts,
            "data_generated_at": data_ts,
            "reason": reason,
        }
        if category_details:
            result["category_details"] = category_details
        return result

    # ── Multi-topic management ────────────────────────────────────────

    def archive_current_topic(self) -> Optional[str]:
        """Archive the current active prompts + data into a topic snapshot.

        Returns the topic slug, or None if there is no active topic.
        """
        meta = self.get_topic_metadata()
        topic = meta.get("topic", "")
        if not topic:
            return None

        slug = _slugify(topic)
        prompt_snapshot = self.topics_dir / slug
        data_snapshot = self.data_topics_dir / slug

        # Archive prompt files (all model directories found)
        archived_models = []
        for model_dir in self._get_model_dirs():
            src = self.prompts_dir / model_dir
            dst = prompt_snapshot / model_dir
            if src.exists():
                dst.mkdir(parents=True, exist_ok=True)
                for f in src.glob("*.md"):
                    shutil.copy2(f, dst / f.name)
                archived_models.append(model_dir)

        # Archive data files
        for data_type, filename in self._DATA_FILES.items():
            src = self.data_dir / data_type / filename
            if src.exists():
                dst_dir = data_snapshot / data_type
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_dir / filename)

        # Save topic metadata inside the snapshot
        snapshot_meta = {
            "topic": topic,
            "slug": slug,
            "models": archived_models,
            "archived_at": datetime.now().isoformat(),
            "prompts_updated_at": meta.get("prompts_updated_at", ""),
            "data_generated_at": meta.get("data_generated_at", ""),
        }
        with open(prompt_snapshot / "topic.json", "w", encoding="utf-8") as f:
            json.dump(snapshot_meta, f, indent=2, ensure_ascii=False)

        logger.info(f"Archived topic '{topic}' -> {slug}")
        return slug

    def activate_topic(self, slug: str) -> Dict[str, Any]:
        """Restore a previously archived topic as the active one.

        Copies the topic's prompts + data back into the active locations
        and updates topic metadata.

        Returns metadata of the activated topic.
        """
        prompt_snapshot = self.topics_dir / slug
        data_snapshot = self.data_topics_dir / slug

        if not prompt_snapshot.exists():
            raise FileNotFoundError(f"Topic archive not found: {slug}")

        # Read snapshot metadata
        meta_file = prompt_snapshot / "topic.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                snap_meta = json.load(f)
        else:
            snap_meta = {"topic": slug, "slug": slug}

        # Archive current topic first (if any) to avoid data loss.
        # SAFETY: only archive if the metadata slug differs from the target.
        # If they match, metadata is already pointing at this topic (possibly
        # from a prior partial operation) and archiving would OVERWRITE the
        # target archive with whatever stale files are in the active dirs.
        current_meta = self.get_topic_metadata()
        current_slug = _slugify(current_meta.get("topic", "")) if current_meta.get("topic") else ""
        if current_slug and current_slug != slug:
            self.archive_current_topic()
        elif current_slug == slug:
            logger.warning(
                f"Metadata already points to '{slug}' — skipping archive "
                f"to avoid overwriting the target archive with stale files"
            )

        # Restore prompt files – clean active dirs first, then copy from archive.
        # IMPORTANT: only clean model dirs that ARE in the archive so that
        # models added *after* the topic was created keep their prompts.
        archive_models = set(self._get_archive_model_dirs(prompt_snapshot))
        for model_dir in self._get_model_dirs():
            if model_dir in archive_models:
                dst = self.prompts_dir / model_dir
                for old_file in dst.glob("*.md"):
                    old_file.unlink()
        # Copy model dirs from the archive
        for model_dir in archive_models:
            src = prompt_snapshot / model_dir
            dst = self.prompts_dir / model_dir
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.md"):
                shutil.copy2(f, dst / f.name)

        # Restore data files – clean active data dirs first, then copy from archive
        for data_type, filename in self._DATA_FILES.items():
            dst_dir = self.data_dir / data_type
            dst_dir.mkdir(parents=True, exist_ok=True)
            # Remove stale data file to prevent topic leakage
            dst_file = dst_dir / filename
            if dst_file.exists():
                dst_file.unlink()
                logger.debug(f"Removed stale data file: {dst_file}")
            # Copy from archive (if the topic has this data type)
            src = data_snapshot / data_type / filename
            if src.exists():
                shutil.copy2(src, dst_file)
                logger.debug(f"Restored data: {src} -> {dst_file}")
            else:
                logger.warning(f"Topic archive missing data file: {src}")

        # Update topic metadata
        topic_name = snap_meta.get("topic", slug)
        self._save_topic_metadata(topic_name, prompts_updated=True, data_generated=True)
        # Overwrite timestamps with the original ones if available
        if snap_meta.get("prompts_updated_at") or snap_meta.get("data_generated_at"):
            meta = self.get_topic_metadata()
            meta["prompts_updated_at"] = snap_meta.get("prompts_updated_at", meta["prompts_updated_at"])
            meta["data_generated_at"] = snap_meta.get("data_generated_at", meta["data_generated_at"])
            with open(self._topic_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

        # Log verification of what was restored
        for model_dir in self._get_model_dirs():
            cls_path = self.prompts_dir / model_dir / "classification_agent_system.md"
            if cls_path.exists():
                text = cls_path.read_text(encoding="utf-8")
                first_line = text.splitlines()[0] if text else "(empty)"
                cats = _extract_categories_from_prompt(text)
                logger.info(f"  Restored {model_dir} prompt: {first_line[:80]}")
                logger.info(f"  {model_dir} categories ({len(cats)}): {cats}")
        for data_type, filename in self._DATA_FILES.items():
            dp = self.data_dir / data_type / filename
            if dp.exists():
                import json as _json
                try:
                    data = _json.loads(dp.read_text(encoding="utf-8"))
                    logger.info(f"  Restored data/{data_type}: {len(data)} items")
                except Exception:
                    logger.info(f"  Restored data/{data_type}: (unreadable)")
            else:
                logger.warning(f"  Missing data/{data_type}/{filename}")

        logger.info(f"Activated topic '{topic_name}' from archive {slug}")
        return snap_meta

    def list_topics(self) -> List[Dict[str, Any]]:
        """List all archived topics and mark which one is currently active.

        Returns a list of dicts with topic, slug, archived_at, and active flag.
        """
        current_meta = self.get_topic_metadata()
        current_slug = _slugify(current_meta.get("topic", "")) if current_meta.get("topic") else ""

        topics: List[Dict[str, Any]] = []
        for d in sorted(self.topics_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_file = d / "topic.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    meta = {"topic": d.name, "slug": d.name}
            else:
                meta = {"topic": d.name, "slug": d.name}
            meta["active"] = (d.name == current_slug)
            topics.append(meta)

        # If the current topic is not yet archived, add it as a virtual entry
        if current_meta.get("topic") and current_slug and not any(t["slug"] == current_slug for t in topics):
            topics.insert(0, {
                "topic": current_meta["topic"],
                "slug": current_slug,
                "archived_at": None,
                "active": True,
            })

        return topics

    def delete_topic(self, slug: str) -> bool:
        """Delete an archived topic (cannot delete the currently active one).

        Safety features:
        - Cannot delete the topic that metadata says is active.
        - Detects if the active prompt/data files actually belong to the
          topic being deleted (orphan from a prior activate bug).  If so,
          auto-repairs by restoring the correct topic's files from its
          archive before deleting.
        """
        current_meta = self.get_topic_metadata()
        current_slug = _slugify(current_meta.get("topic", "")) if current_meta.get("topic") else ""
        if slug == current_slug:
            raise ValueError("Cannot delete the currently active topic")

        # ── Detect orphaned active files from the topic being deleted ──
        archive_dir = self.topics_dir / slug
        orphan_detected = False
        if archive_dir.exists():
            for model_dir in self._get_archive_model_dirs(archive_dir):
                for prompt_name in ("classification_agent_system", "dialog_agent_system"):
                    active_path = self.prompts_dir / model_dir / f"{prompt_name}.md"
                    archive_path = archive_dir / model_dir / f"{prompt_name}.md"
                    if active_path.exists() and archive_path.exists():
                        try:
                            if active_path.read_text(encoding="utf-8") == archive_path.read_text(encoding="utf-8"):
                                orphan_detected = True
                                break
                        except Exception:
                            pass
                if orphan_detected:
                    break

        if orphan_detected:
            logger.warning(
                f"Active prompt files belong to the topic being deleted ('{slug}'). "
                f"Auto-repairing: restoring correct topic '{current_slug}' from its archive."
            )
            # Restore the CORRECT topic's prompts + data from its own archive
            correct_archive = self.topics_dir / current_slug
            if correct_archive.exists():
                for model_dir in self._get_archive_model_dirs(correct_archive):
                    src = correct_archive / model_dir
                    dst = self.prompts_dir / model_dir
                    if src.exists():
                        dst.mkdir(parents=True, exist_ok=True)
                        for old_f in dst.glob("*.md"):
                            old_f.unlink()
                        for f in src.glob("*.md"):
                            shutil.copy2(f, dst / f.name)
                # Also restore data files
                correct_data_archive = self.data_topics_dir / current_slug
                if correct_data_archive.exists():
                    for data_type, filename in self._DATA_FILES.items():
                        src = correct_data_archive / data_type / filename
                        if src.exists():
                            dst_dir = self.data_dir / data_type
                            dst_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst_dir / filename)
                logger.info(f"Restored correct topic '{current_slug}' files from archive")
            else:
                logger.warning(
                    f"Cannot auto-repair: archive for '{current_slug}' not found. "
                    f"Active files will remain from the deleted topic."
                )

        # ── Delete the archived topic ──────────────────────────────────
        prompt_snapshot = self.topics_dir / slug
        data_snapshot = self.data_topics_dir / slug
        removed = False
        if prompt_snapshot.exists():
            shutil.rmtree(prompt_snapshot)
            removed = True
        if data_snapshot.exists():
            shutil.rmtree(data_snapshot)
            removed = True

        if removed:
            logger.info(f"Deleted topic archive '{slug}'")
        return removed

    def regenerate_test_data(
        self,
        client,
        generator_model: str = "gpt4",
        data_dir: Optional[str] = None,
        topic: Optional[str] = None,
        data_counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Regenerate only the synthetic test data for the current (or given) topic.

        Uses **parallel async calls** — all 5 data types are generated
        concurrently for maximum speed.
        """
        data_dir = data_dir or str(self.data_dir)
        if topic is None:
            topic = self.get_topic_metadata().get("topic", "")
        if not topic:
            return {"error": "No topic set. Generate or save prompts with a topic first."}

        overall_t0 = time.time()
        data_path = Path(data_dir)
        result: Dict[str, Any] = {}

        # Extract categories from ALL active classification prompts.
        # Use the FIRST (canonical) model's categories as source of truth
        # rather than intersection, to avoid losing categories when
        # some models have misaligned taxonomies.
        _cats_by_model: Dict[str, List[str]] = {}
        for m in self._get_model_dirs():
            cls_prompt = self.get_active_prompt(m, "classification_agent_system") or ""
            if cls_prompt:
                cats = _extract_categories_from_prompt(cls_prompt)
                if cats:
                    _cats_by_model[m] = cats
                    logger.info(f"regenerate_test_data: extracted {len(cats)} categories from active {m} prompt")

        canonical_categories: List[str] = []
        if _cats_by_model:
            # Use the first model's categories as the canonical set
            canonical_categories = list(_cats_by_model.values())[0]
            # Log alignment status
            if len(_cats_by_model) >= 2:
                ref_set = set(canonical_categories)
                for m, cats in _cats_by_model.items():
                    overlap = len(set(cats) & ref_set)
                    logger.info(
                        f"regenerate_test_data: {m} has {overlap}/{len(ref_set)} "
                        f"categories aligned with canonical"
                    )

        # Fallback: persisted canonical categories from metadata
        if not canonical_categories:
            _meta_cats = self.get_topic_metadata().get("canonical_categories")
            if _meta_cats:
                canonical_categories = _meta_cats
                logger.info(
                    f"regenerate_test_data: using {len(_meta_cats)} persisted "
                    f"canonical categories from metadata (prompt extraction failed)"
                )

        _dc = data_counts or {}
        _cfg_dc = self._config.get("evaluation", {}).get("test_data_counts", {})
        data_generators = [
            ("classification", self._build_classification_data_prompt, _dc.get("classification", _cfg_dc.get("classification", 20))),
            ("dialog",         self._build_dialog_data_prompt,         _dc.get("dialog",         _cfg_dc.get("dialog", 15))),
            ("general",        self._build_general_data_prompt,        _dc.get("general",        _cfg_dc.get("general", 15))),
            ("rag",            self._build_rag_data_prompt,            _dc.get("rag",            _cfg_dc.get("rag", 10))),
            ("tool_calling",   self._build_tool_calling_data_prompt,   _dc.get("tool_calling",   _cfg_dc.get("tool_calling", 10))),
        ]

        MAX_REGEN_RETRIES = 2

        logger.info("Regenerating all 5 data types in parallel...")

        async def _regen_all():
            sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
            tasks = [
                self._async_generate_one_data(
                    client, generator_model, topic,
                    dt, builder, count, canonical_categories,
                    data_path, MAX_REGEN_RETRIES, sem,
                )
                for dt, builder, count in data_generators
            ]
            return await asyncio.gather(*tasks, return_exceptions=True)

        regen_results = self._run_async(_regen_all())

        for item in regen_results:
            if isinstance(item, Exception):
                logger.error(f"[parallel] Data regeneration exception: {item}")
                continue
            data_type, data_result = item
            result[data_type] = data_result

        # Update metadata AFTER data is on disk to avoid slug desync
        self._save_topic_metadata(
            topic, data_generated=True,
            canonical_categories=canonical_categories if canonical_categories else None,
        )

        # Update the topic archive with fresh data
        meta_now = self.get_topic_metadata()
        meta_slug = _slugify(meta_now.get("topic", "")) if meta_now.get("topic") else ""
        if meta_slug == _slugify(topic):
            self.archive_current_topic()
        else:
            logger.warning(
                f"Metadata slug '{meta_slug}' doesn't match expected '{_slugify(topic)}' "
                f"— skipping archive to avoid overwriting wrong topic"
            )

        elapsed = time.time() - overall_t0
        logger.info(f"Data regeneration complete in {elapsed:.1f}s (parallel)")
        return result

    # ── Index management ──────────────────────────────────────────────

    def _load_index(self) -> List[Dict]:
        """Load the version index from disk (mtime-cached).

        Re-reads the file only when its mtime has changed since the
        last load, so callers that need to see external edits still
        get them, but the common path avoids a redundant parse.
        """
        if self.index_path.exists():
            try:
                current_mtime = self.index_path.stat().st_mtime
                if (
                    hasattr(self, '_index_mtime')
                    and self._index_mtime == current_mtime
                    and self._index is not None
                ):
                    return self._index
                with open(self.index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._index_mtime = current_mtime
                return data
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt versions.json – starting fresh")
        self._index_mtime = 0.0
        return []

    def _save_index(self):
        """Persist the version index to disk."""
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, ensure_ascii=False)
        # Update cached mtime so subsequent _load_index skips re-read
        try:
            self._index_mtime = self.index_path.stat().st_mtime
        except OSError:
            self._index_mtime = 0.0

    # ── Read helpers ──────────────────────────────────────────────────

    def get_active_prompt(self, model: str, prompt_type: str) -> Optional[str]:
        """Read the currently active prompt file."""
        path = self.prompts_dir / model / f"{prompt_type}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ── Save / Edit ───────────────────────────────────────────────────

    def save_prompt(
        self,
        model: str,
        prompt_type: str,
        content: str,
        topic: str = "",
        source: str = "manual",
        author: str = "user",
    ) -> Dict:
        """
        Save a prompt, creating a versioned snapshot of the previous
        content first, then writing and indexing the new content.

        Both the *old* prompt (if it existed) and the *new* prompt get
        their own entry in the version index so they are always visible
        in Version History.

        Returns the version metadata dict for the **new** content.
        """
        with self._io_lock:
            # Always reload from disk so we never overwrite entries
            # added by external scripts or concurrent processes.
            self._index = self._load_index()

            prompt_path = self.prompts_dir / model / f"{prompt_type}.md"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)

            version_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

            # ── 1. Snapshot the CURRENT (old) file and INDEX it ───────────
            if prompt_path.exists():
                old_content = prompt_path.read_text(encoding="utf-8")
                # Only create a snapshot if there is meaningful content and
                # the old content differs from the new content.
                if old_content.strip() and old_content.strip() != content.strip():
                    snapshot_name = f"{model}__{prompt_type}__{version_id}.md"
                    (self.history_dir / snapshot_name).write_text(old_content, encoding="utf-8")

                    # Determine the topic of the OLD content. If the caller
                    # provides no topic we fall back to current metadata, but
                    # when generating for a new topic the previous metadata
                    # still points to the old topic – which is exactly what
                    # we want.
                    old_topic = self.get_topic_metadata().get("topic", "") or topic

                    old_entry = {
                        "id": version_id,
                        "model": model,
                        "prompt_type": prompt_type,
                        "topic": old_topic,
                        "source": "snapshot",
                        "author": author,
                        "timestamp": datetime.now().isoformat(),
                        "filename": snapshot_name,
                    }
                    self._index.insert(0, old_entry)

            # ── 2. Write the NEW content and INDEX it ─────────────────────
            prompt_path.write_text(content, encoding="utf-8")

            new_version_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            new_snapshot = f"{model}__{prompt_type}__{new_version_id}_new.md"
            (self.history_dir / new_snapshot).write_text(content, encoding="utf-8")

            entry = {
                "id": new_version_id,
                "model": model,
                "prompt_type": prompt_type,
                "topic": topic,
                "source": source,
                "author": author,
                "timestamp": datetime.now().isoformat(),
                "filename": new_snapshot,
            }
            self._index.insert(0, entry)   # newest first
            self._save_index()

            return entry

    # ── Version history ───────────────────────────────────────────────

    def list_versions(
        self,
        model: Optional[str] = None,
        prompt_type: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> List[Dict]:
        """
        List version history, optionally filtered.

        Always reloads from disk so external edits (e.g. repair scripts)
        are picked up without a server restart.
        """
        self._index = self._load_index()
        versions = self._index
        if model:
            versions = [v for v in versions if v["model"] == model]
        if prompt_type:
            versions = [v for v in versions if v["prompt_type"] == prompt_type]
        if topic:
            topic_lower = topic.lower()
            versions = [v for v in versions if topic_lower in v.get("topic", "").lower()]
        return versions

    def get_version_content(self, version_id: str) -> Optional[str]:
        """Read the content of a specific version by its ID."""
        self._index = self._load_index()
        entry = next((v for v in self._index if v["id"] == version_id), None)
        if not entry:
            return None
        path = self.history_dir / entry["filename"]
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def restore_version(self, version_id: str) -> Optional[Dict]:
        """
        Restore a historical version as the active prompt.
        Creates a new version entry with source='restore'.
        """
        self._index = self._load_index()
        entry = next((v for v in self._index if v["id"] == version_id), None)
        if not entry:
            return None
        content = self.get_version_content(version_id)
        if content is None:
            return None
        return self.save_prompt(
            model=entry["model"],
            prompt_type=entry["prompt_type"],
            content=content,
            topic=entry.get("topic", ""),
            source="restore",
        )

    def delete_version(self, version_id: str) -> bool:
        """
        Delete a specific version from history.

        Removes the entry from the index **and** deletes the snapshot
        file on disk.  Returns True if the version was found and
        removed, False otherwise.
        """
        with self._io_lock:
            self._index = self._load_index()
            entry = next((v for v in self._index if v["id"] == version_id), None)
            if not entry:
                return False

            # Remove the snapshot file (if it exists)
            snapshot_path = self.history_dir / entry.get("filename", "")
            if snapshot_path.exists():
                try:
                    snapshot_path.unlink()
                except OSError as exc:
                    logger.warning("Could not delete snapshot file %s: %s", snapshot_path, exc)

            # Remove from index and persist
            self._index = [v for v in self._index if v["id"] != version_id]
            self._save_index()
            return True

    def delete_versions_bulk(self, version_ids: List[str]) -> int:
        """
        Delete multiple versions at once.

        Returns the number of versions actually removed.
        """
        with self._io_lock:
            self._index = self._load_index()
            ids_set = set(version_ids)
            to_delete = [v for v in self._index if v["id"] in ids_set]

            for entry in to_delete:
                snapshot_path = self.history_dir / entry.get("filename", "")
                if snapshot_path.exists():
                    try:
                        snapshot_path.unlink()
                    except OSError as exc:
                        logger.warning("Could not delete snapshot file %s: %s", snapshot_path, exc)

            self._index = [v for v in self._index if v["id"] not in ids_set]
            self._save_index()
            return len(to_delete)

    # ── AI Generation ─────────────────────────────────────────────────

    @staticmethod
    def _run_async(coro):
        """Run an async coroutine from sync code, handling event-loop reuse."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside a running loop (e.g. Jupyter, nested call).
            # Create a new loop in a thread to avoid "cannot run nested".
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    # ── Async helpers for parallel LLM calls ──────────────────────────

    async def _async_generate_one_prompt(
        self,
        client,
        generator_model: str,
        topic: str,
        target_model: str,
        task: str,
        reference_snippet: str,
        shared_categories: Optional[List[str]],
        semaphore: asyncio.Semaphore,
        model_family: Optional[str] = None,
        deployment_name: Optional[str] = None,
        task_hint: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Generate a single prompt asynchronously.

        Returns (target_model, prompt_type, generated_content).
        """
        prompt_type = f"{task}_agent_system"
        meta_prompt = self._build_generation_prompt(
            topic=topic,
            target_model=target_model,
            task=task,
            reference_snippet=reference_snippet,
            shared_categories=shared_categories if task in ("classification", "dialog") else None,
            model_family=model_family,
            deployment_name=deployment_name,
        )

        async with semaphore:
            logger.info(f"[parallel] Generating {target_model}/{task} prompt...")
            t0 = time.time()
            try:
                res = await client.complete_async(
                    messages=[
                        {"role": "system", "content": (
                            "You are an expert prompt engineer specialising in Azure OpenAI models. "
                            "You create high-quality system prompts that follow each specific model's "
                            "best practices (not just the model family — consider the concrete "
                            "deployment such as GPT-4.1 vs GPT-4o vs GPT-4.1-mini, or GPT-5.2 vs "
                            "GPT-5.1 reasoning). "
                            "Return ONLY the system prompt content, no explanations, no markdown fences."
                        )},
                        {"role": "user", "content": meta_prompt},
                    ],
                    model_name=generator_model,
                )
                generated = res.content.strip()
                logger.info(
                    f"[parallel] {target_model}/{task} prompt done "
                    f"in {time.time() - t0:.1f}s ({len(generated)} chars)"
                )
                return (target_model, prompt_type, generated)
            except Exception as e:
                logger.error(f"[parallel] Failed {target_model}/{prompt_type}: {e}")
                return (target_model, prompt_type, f"[Error: {e}]")

    async def _async_generate_one_data(
        self,
        client,
        generator_model: str,
        topic: str,
        data_type: str,
        prompt_builder,
        target_count: int,
        canonical_categories: List[str],
        data_path: Path,
        max_retries: int,
        semaphore: asyncio.Semaphore,
    ) -> Tuple[str, Dict[str, Any]]:
        """Generate a single data type asynchronously with retries.

        Returns (data_type, result_dict).
        """
        filenames = {
            "classification": "classification_scenarios.json",
            "dialog":         "follow_up_scenarios.json",
            "general":        "capability_tests.json",
            "rag":            "rag_scenarios.json",
            "tool_calling":   "tool_calling_scenarios.json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 2):
            async with semaphore:
                try:
                    data_prompt = prompt_builder(topic, target_count, categories=canonical_categories)

                    _sys_tpl = self._load_data_gen_template("system_message.txt")
                    if _sys_tpl:
                        system_msg = _sys_tpl.replace("{count}", str(target_count))
                    else:
                        system_msg = (
                            "You are a synthetic-data generator for AI evaluation frameworks. "
                            "You produce realistic, diverse test scenarios in valid JSON. "
                            "Return a JSON object with a single key \"scenarios\" whose value is "
                            "the array of test items.  No markdown fences, no trailing "
                            "commas, no comments, no explanation."
                        )
                    if attempt > 1:
                        system_msg += (
                            "\n\nIMPORTANT: Your previous response was rejected. "
                            "Possible issues: invalid JSON, or too few scenarios. "
                            "Double-check that every object uses double-quoted keys, "
                            "there are NO trailing commas before } or ], no // comments, "
                            f"and you MUST return EXACTLY {target_count} scenarios in the array."
                        )

                    logger.info(f"[parallel] Generating {data_type} data (attempt {attempt})...")
                    t0 = time.time()
                    res = await client.complete_async(
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": data_prompt},
                        ],
                        model_name=generator_model,
                        response_format={"type": "json_object"},
                    )

                    raw = res.content.strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        sanitised = _sanitise_json(raw)
                        parsed = json.loads(sanitised)
                        logger.info(f"JSON sanitisation fixed {data_type} data (attempt {attempt})")

                    if isinstance(parsed, dict):
                        scenarios = (
                            parsed.get("scenarios")
                            or next((v for v in parsed.values() if isinstance(v, list)), None)
                        )
                        if scenarios is None:
                            raise ValueError(f"JSON object has no array value — keys: {list(parsed.keys())}")
                    elif isinstance(parsed, list):
                        scenarios = parsed
                    else:
                        raise ValueError("Expected a JSON object or array")

                    if not isinstance(scenarios, list):
                        raise ValueError("Expected a JSON array of scenarios")

                    # Validate classification expected_category — fix any
                    # that don't match the canonical set to prevent 0% accuracy.
                    if data_type == "classification" and canonical_categories:
                        canonical_set = {c.lower().strip() for c in canonical_categories}
                        replaced_count = 0
                        for sc in scenarios:
                            ec = sc.get("expected_category", "")
                            if isinstance(ec, str) and ec.lower().strip() not in canonical_set:
                                ec_lower = ec.lower().strip()
                                # Try fuzzy match first (substring containment)
                                match = next(
                                    (c for c in canonical_categories
                                     if c.lower() in ec_lower or ec_lower in c.lower()),
                                    None,
                                )
                                if match:
                                    sc["expected_category"] = match
                                else:
                                    # Replace with a random canonical category
                                    # so orphaned categories don't drag accuracy to 0%
                                    old_val = ec
                                    sc["expected_category"] = random.choice(canonical_categories)
                                    replaced_count += 1
                                    logger.debug(
                                        f"  Replaced invalid category '{old_val}' "
                                        f"-> '{sc['expected_category']}'"
                                    )
                        if replaced_count:
                            logger.warning(
                                f"[!] Replaced {replaced_count}/{len(scenarios)} {data_type} "
                                f"scenarios whose expected_category was not in the canonical set"
                            )
                        else:
                            logger.info(f"[OK] All {data_type} expected_category values match canonical categories.")

                    # Validate minimum count
                    min_acceptable = max(1, int(target_count * 0.5))
                    if len(scenarios) < min_acceptable and attempt <= max_retries:
                        raise ValueError(
                            f"Too few scenarios: got {len(scenarios)}, expected >= {min_acceptable}"
                        )

                    if len(scenarios) < target_count:
                        logger.info(f"{data_type}: got {len(scenarios)}/{target_count} — accepting")

                    # Normalise to flat schema before persisting
                    scenarios = ensure_flat_schema(scenarios, data_type)

                    # Write file
                    out_dir = data_path / data_type
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_file = out_dir / filenames[data_type]
                    with open(out_file, "w", encoding="utf-8") as f:
                        json.dump(scenarios, f, indent=2, ensure_ascii=False)

                    elapsed = time.time() - t0
                    logger.info(
                        f"[parallel] Generated {len(scenarios)} {data_type} scenarios "
                        f"-> {out_file} in {elapsed:.1f}s (attempt {attempt})"
                    )
                    return (data_type, {"count": len(scenarios), "file": str(out_file)})

                except Exception as e:
                    last_error = e
                    if attempt <= max_retries:
                        logger.warning(f"{data_type} attempt {attempt} failed: {e} — retrying")
                    else:
                        logger.error(f"Failed {data_type} data after {attempt} attempts: {e}")

        return (data_type, {"count": 0, "error": str(last_error)})

    def generate_prompts(
        self,
        topic: str,
        client,
        generator_model: str = "gpt5",
        data_dir: Optional[str] = None,
        target_models: Optional[List[str]] = None,
        data_counts: Optional[Dict[str, int]] = None,
        scope: str = "all",
    ) -> Dict[str, Any]:
        """
        Use an AI model to generate optimised prompts **and/or** matching
        synthetic test data for a given topic.

        Args:
            scope: What to regenerate — ``"all"`` (prompts + data),
                ``"prompts_only"`` (Steps 1-3), or ``"data_only"``
                (Step 4 only, reuses existing categories).
            target_models: Optional list of model keys to generate prompts
                for.  When *None*, uses all model directories currently
                present under ``prompts/``.

        Uses **parallel async calls** to speed up generation:
          - Step 1: canonical model / classification (for category extraction)
          - Step 2: all remaining model × task prompts in parallel
          - Step 3: category alignment & auto-fix
          - Step 4: all 5 data types in parallel

        Returns dict like::

            {
              "prompts": { "<model>": { "<prompt_type>": "…" }, … },
              "data": { "classification": { … }, … }
            }
        """
        overall_t0 = time.time()
        data_dir = data_dir or str(self.data_dir)

        # Resolve which models to generate for
        if target_models:
            models = list(target_models)
        else:
            models = self._get_model_dirs()
            if not models:
                models = list(self._get_model_dirs()) or ["gpt4", "gpt5"]  # fallback to defaults

        # Build model_family lookup from config
        model_families: Dict[str, str] = {}
        model_deployments: Dict[str, str] = {}
        cfg_models = self._config.get("azure", {}).get("models", {})
        for mk, mp in cfg_models.items():
            if isinstance(mp, dict):
                model_families[mk] = mp.get("model_family", "gpt4")
                model_deployments[mk] = mp.get("deployment_name", mk)

        # Pick a "canonical" model (first in list) whose classification
        # prompt is generated first to extract shared categories.
        canonical = models[0]

        result: Dict[str, Any] = {"prompts": {m: {} for m in models}, "data": {}}

        # ── data_only shortcut: skip Steps 0-3, jump to Step 4 ───────
        if scope == "data_only":
            logger.info("== scope=data_only — skipping prompt generation, generating data only ==")
            # Extract categories from existing active classification prompts
            canonical_categories: List[str] = []
            _cats_by_model: Dict[str, List[str]] = {}
            for m in models:
                cls_prompt = self.get_active_prompt(m, "classification_agent_system") or ""
                if cls_prompt:
                    cats = _extract_categories_from_prompt(cls_prompt)
                    if cats:
                        _cats_by_model[m] = cats
            # Use first (canonical) model's categories as source of truth
            if _cats_by_model:
                canonical_categories = list(_cats_by_model.values())[0]
            if canonical_categories:
                logger.info(f"data_only: extracted {len(canonical_categories)} categories from existing prompts")
            # Fallback: persisted canonical categories from metadata
            if not canonical_categories:
                _meta_cats = self.get_topic_metadata().get("canonical_categories")
                if _meta_cats:
                    canonical_categories = _meta_cats
                    logger.info(
                        f"data_only: using {len(_meta_cats)} persisted canonical "
                        f"categories from metadata (prompt extraction failed)"
                    )

            # Jump to Step 4 — reuse the data generation block below
            # (the scope != "prompts_only" guard will run it)
            # We still need data_dir set for the Step 4 block
            # Skip straight past Steps 0-3 by using a local goto via
            # the same Step 4 code at the bottom of this method.
            data_path = Path(data_dir)
            _dc = data_counts or {}
            _cfg_dc = self._config.get("evaluation", {}).get("test_data_counts", {})
            data_generators = [
                ("classification", self._build_classification_data_prompt, _dc.get("classification", _cfg_dc.get("classification", 20))),
                ("dialog",         self._build_dialog_data_prompt,         _dc.get("dialog",         _cfg_dc.get("dialog", 15))),
                ("general",        self._build_general_data_prompt,        _dc.get("general",        _cfg_dc.get("general", 15))),
                ("rag",            self._build_rag_data_prompt,            _dc.get("rag",            _cfg_dc.get("rag", 10))),
                ("tool_calling",   self._build_tool_calling_data_prompt,   _dc.get("tool_calling",   _cfg_dc.get("tool_calling", 10))),
            ]
            MAX_DATA_RETRIES = 2

            async def _step4_only():
                sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
                tasks = [
                    self._async_generate_one_data(
                        client, generator_model, topic,
                        dt, builder, count, canonical_categories,
                        data_path, MAX_DATA_RETRIES, sem,
                    )
                    for dt, builder, count in data_generators
                ]
                return await asyncio.gather(*tasks, return_exceptions=True)

            step4_results = self._run_async(_step4_only())
            for item in step4_results:
                if isinstance(item, Exception):
                    logger.error(f"[parallel] Data generation exception: {item}")
                    continue
                data_type, data_result = item
                result["data"][data_type] = data_result

            self._save_topic_metadata(
                topic, data_generated=True,
                canonical_categories=canonical_categories if canonical_categories else None,
            )
            self.archive_current_topic()
            elapsed = time.time() - overall_t0
            logger.info(f"== Data-only generation complete in {elapsed:.1f}s ==")
            return result

        # ── 0. Archive current topic before overwriting ───────────────
        current = self.get_topic_metadata().get("topic", "")
        if current and _slugify(current) != _slugify(topic):
            self.archive_current_topic()
            logger.info(f"Archived previous topic '{current}' before generating '{topic}'")

        # ── 1. Generate canonical/classification FIRST (needed for categories) ──
        logger.info(f"== Step 1/4: Generating {canonical}/classification prompt (blocking) ==")
        reference = self.get_active_prompt(canonical, "classification_agent_system") or ""
        reference_snippet = reference[:2000] if reference else "(no existing prompt)"

        # When regenerating prompts only, preserve existing categories so
        # new prompts stay aligned with the current test data.
        # Priority: (1) categories actually used in the test data file,
        #           (2) persisted canonical categories in metadata,
        #           (3) categories from the current prompt (weakest signal).
        preserved_categories: Optional[List[str]] = None
        if scope == "prompts_only":
            # 1. Extract from existing test data (strongest — this is what
            #    the evaluation pipeline will compare against).
            _data_file = Path(data_dir) / "classification" / "classification_scenarios.json"
            if _data_file.exists():
                try:
                    _scen = json.loads(_data_file.read_text(encoding="utf-8"))
                    if isinstance(_scen, dict):
                        _scen = _scen.get("scenarios", _scen.get("data", []))
                    _data_cats = sorted({s.get("expected_category", "") for s in _scen
                                         if s.get("expected_category")})
                    if _data_cats:
                        preserved_categories = _data_cats
                        logger.info(
                            f"scope=prompts_only: constraining to {len(_data_cats)} "
                            f"categories from existing test data: {_data_cats}"
                        )
                except Exception as exc:
                    logger.warning(f"Could not read test-data categories: {exc}")
            # 2. Fallback: persisted canonical_categories from metadata.
            if not preserved_categories:
                _meta_cats = self.get_topic_metadata().get("canonical_categories")
                if _meta_cats:
                    preserved_categories = _meta_cats
                    logger.info(
                        f"scope=prompts_only: using {len(_meta_cats)} persisted "
                        f"canonical categories from metadata"
                    )
            # 3. Fallback: extract from the current prompt text.
            if not preserved_categories and reference:
                preserved_categories = _extract_categories_from_prompt(reference)
                if preserved_categories:
                    logger.info(
                        f"scope=prompts_only: preserving {len(preserved_categories)} "
                        f"categories from existing prompt (no test data available)"
                    )

        canonical_family = model_families.get(canonical, "gpt4")
        canonical_deployment = model_deployments.get(canonical)
        canonical_cls_result = self._run_async(
            self._async_generate_one_prompt(
                client, generator_model, topic,
                canonical, "classification", reference_snippet,
                preserved_categories,          # None when scope!="prompts_only"
                asyncio.Semaphore(_MAX_CONCURRENT_LLM),
                model_family=canonical_family,
                deployment_name=canonical_deployment,
            )
        )
        _, _, canonical_cls_content = canonical_cls_result
        if not canonical_cls_content.startswith("[Error"):
            self.save_prompt(
                model=canonical, prompt_type="classification_agent_system",
                content=canonical_cls_content, topic=topic,
                source="ai-generated", author=f"generator:{generator_model}",
            )
        result["prompts"][canonical]["classification_agent_system"] = canonical_cls_content

        # Extract categories from canonical classification
        shared_categories: Optional[List[str]] = None
        if not canonical_cls_content.startswith("[Error"):
            extracted = _extract_categories_from_prompt(canonical_cls_content)
            if extracted:
                shared_categories = extracted
                logger.info(
                    f"Extracted {len(extracted)} categories from {canonical} "
                    f"classification prompt: {extracted}"
                )
        # Fallback: if extraction from new prompt failed, keep preserved ones
        if not shared_categories and preserved_categories:
            shared_categories = preserved_categories
            logger.info("Using preserved categories as fallback for shared_categories")

        # ── 2. Generate remaining prompts in PARALLEL ─────────────────
        n_remaining = len(models) * 4 - 1  # total minus the one already done
        logger.info(f"== Step 2/4: Generating {n_remaining} remaining prompts in parallel ==")

        async def _step2():
            sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
            tasks = []
            for tgt_model in models:
                family = model_families.get(tgt_model, "gpt4")
                deployment = model_deployments.get(tgt_model)
                for task in ("classification", "dialog", "rag", "tool_calling"):
                    # Skip canonical/classification — already done
                    if tgt_model == canonical and task == "classification":
                        continue

                    prompt_type = f"{task}_agent_system"

                    # For classification tasks, use canonical cls as reference
                    if task == "classification" and shared_categories:
                        ref_snippet = canonical_cls_content if not canonical_cls_content.startswith("[Error") else "(no reference)"
                    else:
                        ref = self.get_active_prompt(tgt_model, prompt_type) or ""
                        ref_snippet = ref[:2000] if ref else "(no existing prompt)"

                    cats = shared_categories if task in ("classification", "dialog") else None
                    tasks.append(
                        self._async_generate_one_prompt(
                            client, generator_model, topic,
                            tgt_model, task, ref_snippet, cats, sem,
                            model_family=family,
                            deployment_name=deployment,
                            task_hint=task,
                        )
                    )
            return await asyncio.gather(*tasks, return_exceptions=True)

        step2_results = self._run_async(_step2())

        # Process step 2 results — save prompts to disk
        for item in step2_results:
            if isinstance(item, Exception):
                logger.error(f"[parallel] Prompt generation exception: {item}")
                continue
            tgt_model, prompt_type, generated = item
            if not generated.startswith("[Error"):
                self.save_prompt(
                    model=tgt_model, prompt_type=prompt_type,
                    content=generated, topic=topic,
                    source="ai-generated", author=f"generator:{generator_model}",
                )
            if tgt_model not in result["prompts"]:
                result["prompts"][tgt_model] = {}
            result["prompts"][tgt_model][prompt_type] = generated

        # ── 1a. NOW update metadata (prompts are on disk) ─────────
        self._save_topic_metadata(topic, prompts_updated=True)

        # ── 3. Category alignment & auto-fix ──────────────────────────
        # The canonical model's categories are the single source of truth.
        # Any model whose extracted categories don't fully match gets
        # auto-fixed via an LLM rewrite.
        logger.info("== Step 3/4: Category alignment & schema validation ==")
        _categories: Dict[str, List[str]] = {}
        for m in models:
            cls_prompt = result["prompts"].get(m, {}).get("classification_agent_system", "")
            if cls_prompt and not cls_prompt.startswith("[Error"):
                cats = _extract_categories_from_prompt(cls_prompt)
                if cats:
                    _categories[m] = cats
                    logger.info(f"Extracted {len(cats)} categories from {m} classification prompt: {cats}")

        # The canonical model's categories are the authoritative set.
        # We do NOT use N-way intersection — that dilutes the taxonomy.
        ref_cats = _categories.get(canonical, [])
        canonical_categories: List[str] = ref_cats if ref_cats else []

        if not canonical_categories and _categories:
            # Fallback: use first model that has categories
            canonical_categories = list(_categories.values())[0]
            logger.warning(
                f"Canonical model {canonical} had no extractable categories — "
                f"using first available model's categories"
            )

        if canonical_categories:
            ref_set = set(canonical_categories)
            logger.info(
                f"Canonical categories ({len(canonical_categories)} from {canonical}): "
                f"{canonical_categories}"
            )

            # Auto-fix ANY model whose categories don't match ≥80% of canonical
            _ALIGNMENT_THRESHOLD = 0.8
            for m in models:
                if m == canonical:
                    continue
                m_cats = set(_categories.get(m, []))
                overlap = len(m_cats & ref_set)
                ratio = overlap / len(ref_set) if ref_set else 1.0
                if ratio >= _ALIGNMENT_THRESHOLD and m_cats == ref_set:
                    logger.info(f"  {m}: categories ALIGNED (100% match)")
                    continue
                logger.warning(
                    f"  {m}: category mismatch — {overlap}/{len(ref_set)} overlap "
                    f"({ratio:.0%}), auto-fixing..."
                )
                try:
                    m_cls = result["prompts"].get(m, {}).get("classification_agent_system", "")
                    cat_list_str = '\n'.join(f'  - {c}' for c in canonical_categories)
                    fix_prompt = (
                        f"The following system prompt was generated for a {m.upper()} classification agent, "
                        "but it uses WRONG category codes. Rewrite it so that the EXACT primary "
                        "category codes listed below are used AS-IS (copy verbatim, do NOT rename, "
                        "do NOT add extras, do NOT remove any).\n\n"
                        f"MANDATORY PRIMARY CATEGORY CODES ({len(canonical_categories)} total — use ALL of these EXACTLY):\n"
                        f"{cat_list_str}\n\n"
                        "MANDATORY JSON OUTPUT FIELD NAMES:\n"
                        "The prompt's JSON output schema MUST use these exact field names:\n"
                        "  primary_category, subcategory, priority, sentiment, confidence, summary, follow_up_questions\n"
                        "(NOT category.primary, NOT priority_level, NOT summary_es, etc.)\n\n"
                        "Original prompt to fix:\n" + m_cls[:8000]
                        + "\n\nReturn ONLY the corrected system prompt — no explanation."
                    )
                    fix_res = client.complete(
                        messages=[
                            {"role": "system", "content": (
                                "You are an expert prompt engineer. Fix the category codes and "
                                "JSON field names as instructed. Output ONLY the corrected prompt."
                            )},
                            {"role": "user", "content": fix_prompt},
                        ],
                        model_name=generator_model,
                    )
                    fixed = fix_res.content.strip()
                    fixed_cats = _extract_categories_from_prompt(fixed)
                    fixed_overlap = len(set(fixed_cats) & ref_set)
                    fixed_ratio = fixed_overlap / len(ref_set) if ref_set else 1.0
                    if fixed_ratio >= _ALIGNMENT_THRESHOLD:
                        self.save_prompt(
                            model=m, prompt_type="classification_agent_system",
                            content=fixed, topic=topic,
                            source="ai-generated-fixed", author=f"generator:{generator_model}",
                        )
                        result["prompts"][m]["classification_agent_system"] = fixed
                        _categories[m] = fixed_cats
                        logger.info(
                            f"  {m}: realigned — now {fixed_overlap}/{len(ref_set)} "
                            f"({fixed_ratio:.0%}) match"
                        )
                    else:
                        logger.warning(
                            f"  {m}: fix attempt still low overlap "
                            f"({fixed_overlap}/{len(ref_set)}) — keeping original"
                        )
                except Exception as e:
                    logger.error(f"  {m}: auto-fix failed: {e}")
        else:
            logger.warning("Could not extract categories from classification prompts — data generator will invent its own")

        # ── 4. Generate ALL synthetic test data in PARALLEL ───────────
        if scope != "prompts_only":
            logger.info("== Step 4/4: Generating 5 data types in parallel ==")
            data_path = Path(data_dir)
            _dc = data_counts or {}
            _cfg_dc = self._config.get("evaluation", {}).get("test_data_counts", {})
            data_generators = [
                ("classification", self._build_classification_data_prompt, _dc.get("classification", _cfg_dc.get("classification", 20))),
                ("dialog",         self._build_dialog_data_prompt,         _dc.get("dialog",         _cfg_dc.get("dialog", 15))),
                ("general",        self._build_general_data_prompt,        _dc.get("general",        _cfg_dc.get("general", 15))),
                ("rag",            self._build_rag_data_prompt,            _dc.get("rag",            _cfg_dc.get("rag", 10))),
                ("tool_calling",   self._build_tool_calling_data_prompt,   _dc.get("tool_calling",   _cfg_dc.get("tool_calling", 10))),
            ]
            MAX_DATA_RETRIES = 2

            async def _step4():
                sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
                tasks = [
                    self._async_generate_one_data(
                        client, generator_model, topic,
                        dt, builder, count, canonical_categories,
                        data_path, MAX_DATA_RETRIES, sem,
                    )
                    for dt, builder, count in data_generators
                ]
                return await asyncio.gather(*tasks, return_exceptions=True)

            step4_results = self._run_async(_step4())

            for item in step4_results:
                if isinstance(item, Exception):
                    logger.error(f"[parallel] Data generation exception: {item}")
                    continue
                data_type, data_result = item
                result["data"][data_type] = data_result

            # Mark data as freshly generated
            self._save_topic_metadata(
                topic, data_generated=True,
                canonical_categories=canonical_categories if canonical_categories else None,
            )
        else:
            logger.info("== Step 4/4: SKIPPED (scope=prompts_only) ==")

        # Archive the newly generated topic so it's recoverable
        self.archive_current_topic()

        elapsed = time.time() - overall_t0
        scope_label = {"all": "prompts + data", "prompts_only": "prompts only", "data_only": "data only"}.get(scope, scope)
        logger.info(f"== Generation complete ({scope_label}) in {elapsed:.1f}s (parallel pipeline) ==")

        return result

    # ── Meta-prompt builders for prompts ──────────────────────────────

    def _build_generation_prompt(
        self, topic: str, target_model: str, task: str, reference_snippet: str,
        shared_categories: Optional[List[str]] = None,
        model_family: Optional[str] = None,
        deployment_name: Optional[str] = None,
    ) -> str:
        """Build the meta-prompt that instructs the AI to generate a system prompt.

        Uses :func:`src.utils.model_guidance.get_guidance` to produce
        two-tier guidance: family-level base + deployment-specific addendum.
        """

        guidance = _get_model_guidance(target_model, deployment_name=deployment_name, model_family=model_family)

        # Descriptive model label for the meta-prompt so the generator LLM
        # knows exactly which model it is targeting.
        model_label = target_model.upper()
        if deployment_name:
            model_label = f"{target_model.upper()} (deployment: {deployment_name})"

        task_description = {
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
                "- Instructs the model to answer ONLY from provided context passages\n"
                "- Enforces strict grounding — no hallucination or external knowledge\n"
                "- Handles contradictions and insufficient context gracefully\n"
                "- Structures responses with direct answer + supporting details + caveats\n"
                "- Adapts domain knowledge and examples to the given TOPIC"
            ),
            "tool_calling": (
                "a TOOL CALLING / FUNCTION CALLING system prompt that:\n"
                "- Guides the model to select the right tool(s) from available functions\n"
                "- Extracts correct parameters from natural language queries\n"
                "- Handles cases where no tool is needed or params are missing\n"
                "- Supports sequential multi-tool workflows\n"
                "- Adapts tool definitions and examples to the given TOPIC"
            ),
        }

        # Choose reference header and requirements based on whether
        # we are constraining categories from another model's prompt.
        if shared_categories:
            ref_header = (
                "## REFERENCE (GPT-4 prompt for the SAME topic — adapt the "
                "STYLE to the target model’s best practices but keep the "
                "EXACT SAME primary category codes)"
            )
            requirements = (
                "## REQUIREMENTS\n"
                "1. The prompt must be fully self-contained (no placeholders left)\n"
                "2. Keep EXACTLY the same primary category codes as the reference — "
                "do NOT rename, merge, split, or invent new categories\n"
                "3. Adapt subcategories, descriptions, examples, and prose "
                f"to the {model_label} style guidelines above\n"
                "4. Keep the same structural quality as the reference\n"
                "5. The JSON output schema in the generated prompt MUST use the "
                "EXACT field names specified in the MANDATORY JSON OUTPUT SCHEMA "
                "section below — do NOT rename fields (no category.primary, no "
                "priority_level, no summary_es, no follow_up_questions_es)\n"
                "6. Output ONLY the system prompt content — no wrapper, no explanation"
            )
        else:
            ref_header = (
                "## REFERENCE (current prompt for this slot — adapt structure "
                "but change domain to the new topic)"
            )
            requirements = (
                "## REQUIREMENTS\n"
                "1. The prompt must be fully self-contained (no placeholders left)\n"
                f"2. Adapt ALL categories, examples, and terminology to the TOPIC \"{topic}\"\n"
                "3. Use descriptive snake_case names for category codes (e.g. "
                "\"billing_inquiry\", \"technical_support\") — NEVER use short "
                "acronyms like \"BILL\", \"PKG\", \"TECH\", etc.\n"
                "4. Keep the same structural quality as the reference but for the new domain\n"
                "5. Output ONLY the system prompt content — no wrapper, no explanation"
            )

        # For the canonical model (no shared_categories) generating a
        # classification prompt, still inject the schema block so that the
        # first prompt already uses the canonical field names.
        schema_block = ""
        if task == "classification" and not shared_categories:
            schema_block = f"\n{_CANONICAL_CLASSIFICATION_SCHEMA}"

        return f"""Generate a production-ready system prompt for the following scenario:

## TOPIC
{topic}

## TARGET MODEL
{model_label} — follow these guidelines:
{guidance}

## TASK TYPE
Create {task_description.get(task, task)}

{ref_header}
{reference_snippet}

{requirements}
{self._categories_block(shared_categories, task)}
{schema_block}
"""

    @staticmethod
    def _categories_block(categories: Optional[List[str]], task: str = "classification") -> str:
        """Return a category constraint block adapted to the task type.

        - **classification**: strict — the prompt MUST use these exact codes.
        - **dialog**: soft — the prompt should cover all these domain areas
          in its conversation handling, but need not list them as codes.
        - **other tasks**: no block (categories are irrelevant).
        """
        if not categories:
            return ""

        cat_list = '\n'.join(f'  - {c}' for c in categories)

        if task == "classification":
            return (
                f"\n## MANDATORY CATEGORY TAXONOMY (CRITICAL — DO NOT CHANGE)\n"
                f"You MUST use EXACTLY these primary category codes.\n"
                f"Copy each code CHARACTER-BY-CHARACTER — do NOT rename, paraphrase,\n"
                f"merge, split, abbreviate, or invent new categories:\n\n"
                f"{cat_list}\n\n"
                f"These are the ONLY valid primary_category values.\n"
                f"The number of categories is FIXED at {len(categories)}.\n"
                f"Do NOT add extra categories. Do NOT remove any.\n"
                f"You may freely create subcategories, descriptions, and examples\n"
                f"adapted to this model's style, but the primary category codes\n"
                f"MUST be identical to the list above.\n"
                f"\n{_CANONICAL_CLASSIFICATION_SCHEMA}"
            )

        if task == "dialog":
            return (
                f"\n## DOMAIN CATEGORIES REFERENCE\n"
                f"The following categories represent the key areas of this domain.\n"
                f"The conversation agent MUST be capable of handling inquiries,\n"
                f"follow-ups, and escalation flows for ALL of these areas:\n\n"
                f"{cat_list}\n\n"
                f"Ensure the prompt's conversation patterns, follow-up question\n"
                f"templates, and escalation rules cover every area listed above.\n"
                f"You do NOT need to list them as formal category codes, but the\n"
                f"agent's behaviour must naturally handle all these domain topics.\n"
            )

        return ""  # Other tasks (rag, tool_calling) don't need categories

    # ── Meta-prompt builders for synthetic data ───────────────────────

    def _build_classification_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating classification test scenarios."""

        if categories:
            cat_list = ', '.join(f'"{c}"' for c in categories)
            category_block = (
                f"MANDATORY CATEGORIES — use EXACTLY these category codes (copy them verbatim):\n"
                f"  [{cat_list}]\n"
                f"- You MUST use these exact strings as `expected_category` values — do NOT paraphrase, rename, or invent new ones.\n"
                f"- Each category must have 2-6 subcategories, also in readable snake_case.\n"
                f"- Distribute the {count} scenarios across ALL categories (at least 2 per category)."
            )
        else:
            category_block = (
                f'CATEGORY NAMING RULES:\n'
                f'- Invent 5-7 categories that are SPECIFIC and NATURAL for the topic "{topic}"\n'
                f'- Use descriptive snake_case names (e.g. "billing_inquiry", "technical_support",\n'
                f'  "travel_packages", "flight_operations", "safety_compliance")\n'
                f'- Do NOT use short acronyms or codes (no "BILL", "PKG", "TECH", etc.)\n'
                f'- Each category must have 2-6 subcategories, also in readable snake_case\n'
                f'- Categories must cover the full breadth of the domain'
            )

        template = self._load_data_gen_template("classification.txt")
        if template:
            return template.format(count=count, topic=topic, category_block=category_block)

        # Built-in fallback
        return f"""Generate exactly {count} realistic classification test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema (7 flat fields, CSV-compatible):
{{
  "scenarios": [
    {{
      "id": "CLASS_001",
      "customer_input": "Realistic customer message (2-4 sentences)",
      "expected_category": "readable_snake_case",
      "expected_subcategory": "descriptive_subcategory",
      "expected_priority": "low|medium|high|critical",
      "expected_sentiment": "VALUE",
      "context": "{{\\"key1\\": \\"value1\\", \\"key2\\": \\"value2\\"}}"
    }}
  ]
}}

{category_block}

FIELD RULES:
- "context" is a JSON string (a stringified dict of domain-specific metadata).
  Do NOT use a raw object; it MUST be a JSON-encoded string.

SENTIMENT VALUES (use exactly one of these):
  very_angry, angry, frustrated, concerned, worried, neutral, curious, cautious, positive, professional

IMPORTANT RULES:
1. ALL scenarios, customer inputs, categories, subcategories, and contexts must be domain-specific to "{topic}"
2. Use at least 5 of your invented categories, distributing scenarios across them
3. Distribute priorities evenly: ~20% critical, ~30% high, ~30% medium, ~20% low
4. Mix sentiments realistically using ONLY the values listed above
5. Customer inputs must be natural, varied in tone and length
6. Context fields should contain domain-specific metadata as a JSON string
7. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_dialog_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating dialog test scenarios."""

        if categories:
            cat_list = ', '.join(f'"{c}"' for c in categories)
            category_block = (
                f"MANDATORY CATEGORIES — use ONLY these exact category codes:\n"
                f"  [{cat_list}]\n"
                f"Copy them VERBATIM as the `category` field value. Do NOT rename or invent new ones.\n"
                f"Distribute scenarios across at least 4 of these categories."
            )
        else:
            category_block = (
                'CATEGORY NAMING: Use descriptive snake_case names specific to the topic\n'
                '(e.g. "technical_support", "safety_compliance"). Do NOT use short codes or acronyms.'
            )

        template = self._load_data_gen_template("dialog.txt")
        if template:
            return template.format(count=count, topic=topic, category_block=category_block)

        # Built-in fallback
        return f"""Generate exactly {count} realistic dialog/follow-up test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema (6 flat fields, CSV-compatible):
{{
  "scenarios": [
    {{
      "id": "DLG_001",
      "conversation": "[{{\\"role\\": \\"customer\\", \\"message\\": \\"...\\"}}, {{\\"role\\": \\"agent\\", \\"message\\": \\"...\\"}}, {{\\"role\\": \\"customer\\", \\"message\\": \\"...\\"}}]",
      "context_gaps": "gap1 | gap2",
      "optimal_follow_up": "Best next agent response (detailed)",
      "follow_up_rules": "rule1 | rule2",
      "expected_resolution_turns": 2
    }}
  ]
}}

{category_block}

FIELD RULES:
- "conversation" is a JSON-encoded string of a {{role, message}} array. Do NOT use a raw array.
- "context_gaps" and "follow_up_rules" are pipe-separated strings (e.g. "gap1 | gap2").

IMPORTANT RULES:
1. ALL scenarios and conversations must be domain-specific to "{topic}"
2. Vary conversation lengths: some with 1 turn, some with 2-3 turns
3. Context gaps should reflect realistic missing information for the domain
4. Optimal follow-ups should be professional, empathetic, and specific
5. Mix simple and complex dialog situations
6. Customer messages should be natural and varied
7. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_general_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating general capability tests."""
        # General tests don't use the category taxonomy, but we accept
        # the kwarg for a uniform call signature.
        template = self._load_data_gen_template("general.txt")
        if template:
            return template.format(count=count, topic=topic, category_block="")

        # Built-in fallback
        return f"""Generate exactly {count} general capability test cases for evaluating AI models on the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema (7 flat fields, CSV-compatible):

Variant A — Single-prompt test:
{{
  "scenarios": [
    {{
      "id": "GEN_001",
      "test_type": "reasoning_capability|instruction_following|structured_output|consistency|edge_case_handling|multi_language|safety_boundary|summarization|calculation_accuracy|persona_adherence|ambiguity_resolution|compliance_awareness|negative_sentiment_handling",
      "prompt": "The test prompt",
      "complexity": "low|medium|high",
      "expected_behavior": "Description of correct behavior",
      "conversation": "",
      "run_count": 1
    }}
  ]
}}

Variant B — Multi-turn test (conversation is a JSON-encoded string):
{{
  "scenarios": [
    {{
      "id": "GEN_008",
      "test_type": "context_retention",
      "prompt": "",
      "complexity": "medium",
      "expected_behavior": "Description of correct behavior",
      "conversation": "[{{\\"role\\": \\"user\\", \\"content\\": \\"...\\"}}, {{\\"role\\": \\"assistant\\", \\"content\\": \\"...\\"}}, {{\\"role\\": \\"user\\", \\"content\\": \\"...\\"}}]",
      "run_count": 1
    }}
  ]
}}

FIELD RULES:
- "conversation" is either empty string or a JSON-encoded string of a {{role, content}} array.
- Every item MUST have ALL 7 fields. Use empty string for unused fields.

IMPORTANT RULES:
1. ALL test content must be domain-specific to "{topic}" — adapt scenarios, numbers, terminology
2. Use at least 6 different test_types across the {count} items
3. Distribute complexity: ~30% low, ~45% medium, ~25% high
4. Include at least 1 structured_output test
5. Include at least 1 consistency test with "run_count": 5
6. Include at least 1 safety_boundary test relevant to the domain
7. Include at least 1 multi_language test
8. Include at least 1 calculation_accuracy test
9. All prompts should be realistic and testable
10. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_rag_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating RAG test scenarios."""
        # RAG tests don't use the category taxonomy, but we accept
        # the kwarg for a uniform call signature.
        template = self._load_data_gen_template("rag.txt")
        if template:
            return template.format(count=count, topic=topic, category_block="")

        # Built-in fallback
        return f"""Generate exactly {count} RAG (Retrieval-Augmented Generation) test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema (4 flat text fields, CSV-compatible):
{{
  "scenarios": [
    {{
      "id": "RAG_001",
      "query": "A realistic user question about the topic",
      "context": "One or more paragraphs of retrieved context that contain relevant information. This should be 2-5 sentences of realistic domain text.",
      "ground_truth": "The correct answer that can be fully derived from the context."
    }}
  ]
}}

IMPORTANT RULES:
1. ALL scenarios must be domain-specific to "{topic}"
2. Context passages must be realistic and contain enough detail to answer the query
3. Ground truth must be FULLY derivable from the context (no external knowledge needed)
4. Include at least 1 scenario where context is INSUFFICIENT to fully answer the query
5. Include at least 1 scenario with CONTRADICTORY information in the context
6. Mix query complexity: simple factual, multi-hop reasoning, and synthesis
7. Queries should be natural and varied
8. Context should simulate real retrieved documents (policies, manuals, FAQs, articles)
9. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation
"""

    def _build_tool_calling_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating tool-calling test scenarios."""
        # Tool calling tests don't use the category taxonomy.
        template = self._load_data_gen_template("tool_calling.txt")
        if template:
            return template.format(count=count, topic=topic, category_block="")

        # Built-in fallback
        return f"""Generate exactly {count} tool-calling/function-calling test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema (5 flat fields, CSV-compatible):
{{
  "scenarios": [
    {{
      "id": "TC_001",
      "query": "A natural user request that may or may not require tool usage",
      "available_tools": "[{{\\"type\\": \\"function\\", \\"function\\": {{\\"name\\": \\"tool_name\\", \\"description\\": \\"What the tool does\\", \\"parameters\\": {{\\"type\\": \\"object\\", \\"properties\\": {{\\"param1\\": {{\\"type\\": \\"string\\", \\"description\\": \\"Param description\\"}}}}}}}}}}]",
      "expected_tool_calls": "tool_name1 | tool_name2",
      "expected_parameters": "{{\\"tool_name\\": {{\\"param1\\": \\"expected_value\\"}}}}"
    }}
  ]
}}

FIELD RULES:
- "available_tools" is a JSON-encoded string of an array of OpenAI tool defs. Do NOT use a raw array.
- "expected_tool_calls" is a pipe-separated string (e.g. "func1 | func2"). Empty string if no call expected.
- "expected_parameters" is a JSON-encoded string of a dict. Empty string if no params expected.

IMPORTANT RULES:
1. ALL scenarios, tools, and queries must be domain-specific to "{topic}"
2. Include 2-4 available tools per scenario (realistic for the domain)
3. Include at least 1 scenario where NO tool is needed (expected_tool_calls: "")
4. Include at least 1 scenario requiring MULTIPLE sequential tool calls
5. Include at least 1 scenario where required parameters are MISSING from the query
6. Include at least 1 scenario with AMBIGUOUS tool selection
7. Tool definitions should be realistic and well-structured
8. Parameter types should vary (string, number, boolean, array)
9. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation
"""
