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
    the active locations (prompts/gpt4, gpt5, data/synthetic/*).
  - Generating a new topic automatically archives the current one first.
"""

import asyncio
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import logging

logger = logging.getLogger(__name__)

# Maximum number of concurrent LLM calls to avoid API rate-limit errors.
_MAX_CONCURRENT_LLM = 5


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


def _extract_categories_from_prompt(prompt_text: str) -> List[str]:
    """Extract top-level category codes from a generated classification prompt.

    Supports many formats the generator may produce:

    1. **Markdown table** — Any table under a heading containing
       "categor", "taxonom", etc.  Detects the category column by
       header name *or* by picking the first column whose data rows
       contain snake_case codes.

    2. **YAML dict keys** under a ``categories:`` line.

    3. **Bullet / dash lists** like ``- booking_management`` under a
       taxonomy heading, optionally followed by indented sub-items.

    Returns a deduplicated list preserving first-occurrence order,
    excluding ``out_of_scope`` and similar sentinel values.
    """
    categories: list[str] = []
    seen: set[str] = set()

    noise = {
        'out_of_scope', 'other_or_unclear', 'out_of_scope_non_aerodynamic',
        'out_of_scope_or_non_aeronautics', 'out_of_scope_or_general',
    }

    _CODE_RE = re.compile(r'^[a-z][a-z0-9_]{2,}$')

    def _add(code: str) -> None:
        if code and code not in seen and code not in noise:
            seen.add(code)
            categories.append(code)

    lines = prompt_text.splitlines()

    # ── Strategy 1: Markdown tables ───────────────────────────────
    # Look for tables inside sections whose heading mentions categories
    # / taxonomy.  Detect the code column flexibly.
    in_taxonomy_section = False
    in_table = False
    cat_col_idx: Optional[int] = None
    separator_seen = False

    for line in lines:
        stripped = line.strip()

        # Detect section headings
        if stripped.startswith('#'):
            heading_lower = stripped.lower()
            # Check EXCLUSION keywords first — these sections look like
            # taxonomy tables (snake_case codes) but are NOT categories.
            # Must be tested BEFORE the inclusion check because headings
            # like "SENTIMENT CLASSIFICATION" match both patterns.
            is_excluded = bool(re.search(
                r'(priorid|priority|sentim|output|format|ejemplo|example|instruc|entity|risk|follow)',
                heading_lower))
            # A heading like "subcategorías por categoría" is purely about
            # subcategories — skip.  But "categorías y subcategorías" or
            # "taxonomía de categorías" IS a taxonomy section, so only
            # reject when the heading is EXCLUSIVELY about subcategories.
            # NOTE: The exception regex must cover BOTH English and Spanish:
            #   English: "categories and subcategories", "primary categories"
            #   Spanish: "categorías y subcategorías"
            is_sub_only = bool(re.search(r'(secondary|sub\s*categor)', heading_lower)) \
                and not re.search(r'(taxonom|categor[ií](as?|es)\s+(y|and|principal)|primary\s+categor)', heading_lower)
            if is_excluded or is_sub_only:
                in_taxonomy_section = False
            elif re.search(r'(categor|taxonom|classif)', heading_lower):
                in_taxonomy_section = True
            in_table = False
            cat_col_idx = None
            separator_seen = False
            continue

        if not in_taxonomy_section:
            continue

        # Detect table header row
        if '|' in stripped and not in_table:
            cols = [c.strip().lower() for c in stripped.split('|')]
            # Pass 1: look for a column explicitly named as a code column
            # (e.g. "Code", "category_code", "código").  This is the most
            # reliable signal and must be checked across ALL columns first.
            for i, col in enumerate(cols):
                if col in ('code', 'category_code', 'codigo', 'código',
                           'category code', 'código de categoría'):
                    cat_col_idx = i
                    in_table = True
                    break
            # Pass 2: if no explicit code column, look for a column whose
            # header mentions "categoría principal", "primary category", etc.
            # Exclude bare "category" — that's usually the display-name col.
            if not in_table:
                for i, col in enumerate(cols):
                    if re.search(r'categor[ií]a.*principal|primary.*categ|category\s*(code|cod)', col):
                        cat_col_idx = i
                        in_table = True
                        break
            # Pass 3: match "Categoría principal (category)" style headers
            if not in_table:
                for i, col in enumerate(cols):
                    if re.search(r'categor[ií]a.*\(', col):
                        cat_col_idx = i
                        in_table = True
                        break
            # If no explicit column found, tentatively enter table mode
            # and auto-detect the first column with snake_case codes
            if not in_table and len([c for c in cols if c.strip()]) >= 2:
                in_table = True
                cat_col_idx = None  # will auto-detect from data rows
                separator_seen = False
            continue

        # Skip separator row (|---|---|...)
        if in_table and re.match(r'^[\s|:-]+$', stripped):
            separator_seen = True
            continue

        # Extract from data rows
        if in_table and '|' in stripped and separator_seen:
            cols = [c.strip().strip('`') for c in stripped.split('|')]
            if cat_col_idx is not None:
                if cat_col_idx < len(cols):
                    code = cols[cat_col_idx].strip()
                    if _CODE_RE.match(code):
                        _add(code)
            else:
                # Auto-detect: find first column with a snake_case code
                for i, col_val in enumerate(cols):
                    code = col_val.strip()
                    if _CODE_RE.match(code):
                        cat_col_idx = i
                        _add(code)
                        break
        elif in_table and '|' not in stripped and stripped:
            # End of current table.  If we already extracted codes from
            # this table, assume it was the PRIMARY categories table and
            # stop scanning further tables in this section (which would
            # be subcategory tables).
            if categories:
                in_taxonomy_section = False
            in_table = False
            cat_col_idx = None
            separator_seen = False

    # ── Strategy 2: YAML dict keys under `categories:` ───────────
    yaml_meta_keys = {'name', 'description', 'subcategories', 'priority', 'sentiment',
                      'priority_levels', 'sentiment_values', 'entity_schema',
                      'follow_up_question_types'}

    cat_header_re = re.compile(r'^(\s*)categories:\s*$', re.MULTILINE)
    for m in cat_header_re.finditer(prompt_text):
        base_indent = len(m.group(1))
        cat_indent_min = base_indent + 1
        cat_indent_max = base_indent + 6
        pos = m.end()
        remaining = prompt_text[pos:].splitlines()

        in_subcategories = False
        subcat_base_indent: Optional[int] = None

        for sub_line in remaining:
            if not sub_line.strip():
                continue
            content = sub_line.lstrip()
            indent = len(sub_line) - len(content)

            if indent <= base_indent and content and not content.startswith('#'):
                break

            if content.rstrip().rstrip(':') == 'subcategories':
                in_subcategories = True
                subcat_base_indent = indent
                continue

            if in_subcategories and subcat_base_indent is not None and indent <= subcat_base_indent:
                in_subcategories = False
                subcat_base_indent = None

            if in_subcategories:
                continue

            if cat_indent_min <= indent <= cat_indent_max:
                key_m = re.match(r'([a-z][a-z0-9_]{2,}):', content)
                if key_m and key_m.group(1) not in yaml_meta_keys:
                    _add(key_m.group(1))

    # ── Strategy 3: Bullet / dash lists ───────────────────────────
    # Lines like "- booking_management" or "- booking_management:" under
    # a taxonomy heading, where subcategories are indented deeper.
    if not categories:
        in_tax = False
        list_indent: Optional[int] = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#'):
                heading_lower = stripped.lower()
                # Same heading logic as Strategy 1: check exclusion first
                is_excluded = bool(re.search(
                    r'(priorid|priority|sentim|output|format|entity|risk|follow|ejemplo|example|instruc)',
                    heading_lower))
                is_sub_only = bool(re.search(r'(secondary|sub\s*categor)', heading_lower)) \
                    and not re.search(r'(taxonom|categor[ií]as?\s+(y|and|principal))', heading_lower)
                if is_excluded or is_sub_only:
                    in_tax = False
                elif re.search(r'(categor|taxonom|classif)', heading_lower):
                    in_tax = True
                    list_indent = None
                continue
            if not in_tax:
                continue
            # Match "- category_code" or "- category_code:" at the top
            # indent level for lists (ignore deeper-indented sub-items)
            m = re.match(r'^(\s*)[-*]\s+([a-z][a-z0-9_]{2,}):?\s*$', line)
            if m:
                indent = len(m.group(1))
                if list_indent is None:
                    list_indent = indent
                if indent == list_indent:
                    _add(m.group(2))
            # Also match "primary_category options" style list items
            m2 = re.match(r'^\s*[-*]\s+\**([a-z][a-z0-9_]{2,})\**\s*$', line)
            if m2 and list_indent is not None:
                pass  # already handled above

    return categories


class PromptManager:
    """
    Manages prompt templates with version history, AI-powered generation,
    and multi-topic support.

    Directory structure:
        prompts/
        ├── gpt4/                                (active prompts)
        │   ├── classification_agent_system.md
        │   └── dialog_agent_system.md
        ├── gpt5/
        │   ├── classification_agent_system.md
        │   └── dialog_agent_system.md
        ├── topics/                              (archived topic snapshots)
        │   ├── telco_customer_service/
        │   │   ├── gpt4/ ...
        │   │   ├── gpt5/ ...
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

    def __init__(self, prompts_dir: str = "prompts", data_dir: str = "data/synthetic"):
        self.prompts_dir = Path(prompts_dir)
        self.data_dir = Path(data_dir)
        self.history_dir = self.prompts_dir / "history"
        self.topics_dir = self.prompts_dir / "topics"
        self.data_topics_dir = self.data_dir / "topics"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        self.data_topics_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.history_dir / "versions.json"
        self._topic_path = self.history_dir / "topic_metadata.json"
        self._index = self._load_index()

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

    def _save_topic_metadata(self, topic: str, *, prompts_updated: bool = False, data_generated: bool = False):
        """Persist topic metadata to disk."""
        meta = self.get_topic_metadata()
        if topic:
            meta["topic"] = topic
        now = datetime.now().isoformat()
        if prompts_updated:
            meta["prompts_updated_at"] = now
        if data_generated:
            meta["data_generated_at"] = now
        with open(self._topic_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def is_data_in_sync(self) -> Dict[str, Any]:
        """Check whether synthetic test data is up-to-date with the prompts.

        Returns a dict with ``in_sync`` (bool), ``topic``, and timestamps.
        Data is considered out-of-sync when prompts have been updated
        *after* the last data generation, or when data has never been
        generated for the current topic.
        """
        meta = self.get_topic_metadata()
        topic = meta.get("topic", "")
        prompts_ts = meta.get("prompts_updated_at", "")
        data_ts = meta.get("data_generated_at", "")

        if not topic:
            return {"in_sync": True, "topic": "", "reason": "no_topic_set"}

        if not data_ts:
            return {"in_sync": False, "topic": topic, "reason": "data_never_generated"}

        in_sync = data_ts >= prompts_ts if prompts_ts else True
        reason = "up_to_date" if in_sync else "prompts_updated_after_data"
        return {
            "in_sync": in_sync,
            "topic": topic,
            "prompts_updated_at": prompts_ts,
            "data_generated_at": data_ts,
            "reason": reason,
        }

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

        # Archive prompt files
        for model_dir in ("gpt4", "gpt5"):
            src = self.prompts_dir / model_dir
            dst = prompt_snapshot / model_dir
            if src.exists():
                dst.mkdir(parents=True, exist_ok=True)
                for f in src.glob("*.md"):
                    shutil.copy2(f, dst / f.name)

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

        # Restore prompt files – clean active dirs first, then copy from archive
        for model_dir in ("gpt4", "gpt5"):
            src = prompt_snapshot / model_dir
            dst = self.prompts_dir / model_dir
            if src.exists():
                dst.mkdir(parents=True, exist_ok=True)
                # Remove existing prompt files to avoid stale leftovers
                for old_file in dst.glob("*.md"):
                    old_file.unlink()
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
        for model_dir in ("gpt4", "gpt5"):
            cls_path = self.prompts_dir / model_dir / "classification_agent_system.md"
            if cls_path.exists():
                first_line = cls_path.read_text(encoding="utf-8").splitlines()[0] if cls_path.stat().st_size > 0 else "(empty)"
                cats = _extract_categories_from_prompt(cls_path.read_text(encoding="utf-8"))
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
            for model_dir in ("gpt4", "gpt5"):
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
                for model_dir in ("gpt4", "gpt5"):
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
        data_dir: str = "data/synthetic",
        topic: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate only the synthetic test data for the current (or given) topic.

        Uses **parallel async calls** — all 5 data types are generated
        concurrently for maximum speed.
        """
        if topic is None:
            topic = self.get_topic_metadata().get("topic", "")
        if not topic:
            return {"error": "No topic set. Generate or save prompts with a topic first."}

        overall_t0 = time.time()
        data_path = Path(data_dir)
        result: Dict[str, Any] = {}

        # Extract categories from BOTH active classification prompts and
        # use the intersection so that test data works for both models.
        _cats_by_model: Dict[str, List[str]] = {}
        for m in ("gpt5", "gpt4"):
            cls_prompt = self.get_active_prompt(m, "classification_agent_system") or ""
            if cls_prompt:
                cats = _extract_categories_from_prompt(cls_prompt)
                if cats:
                    _cats_by_model[m] = cats
                    logger.info(f"regenerate_test_data: extracted {len(cats)} categories from active {m} prompt")

        canonical_categories: List[str] = []
        if "gpt5" in _cats_by_model and "gpt4" in _cats_by_model:
            set5 = set(_cats_by_model["gpt5"])
            set4 = set(_cats_by_model["gpt4"])
            common = [c for c in _cats_by_model["gpt4"] if c in set5]
            if common:
                canonical_categories = common
                logger.info(f"regenerate_test_data: using INTERSECTION ({len(common)} categories): {common}")
            else:
                canonical_categories = _cats_by_model["gpt4"]
                logger.warning(
                    f"regenerate_test_data: no category overlap — "
                    f"using GPT-4 categories as source of truth"
                )
        elif _cats_by_model:
            canonical_categories = list(_cats_by_model.values())[0]

        data_generators = [
            ("classification", self._build_classification_data_prompt, 20),
            ("dialog",         self._build_dialog_data_prompt,         15),
            ("general",        self._build_general_data_prompt,        15),
            ("rag",            self._build_rag_data_prompt,            10),
            ("tool_calling",   self._build_tool_calling_data_prompt,   10),
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
        self._save_topic_metadata(topic, data_generated=True)

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
        """Load the version index from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt versions.json – starting fresh")
        return []

    def _save_index(self):
        """Persist the version index to disk."""
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, ensure_ascii=False)

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
            shared_categories=shared_categories if task == "classification" else None,
        )

        async with semaphore:
            logger.info(f"[parallel] Generating {target_model}/{task} prompt...")
            t0 = time.time()
            try:
                res = await client.complete_async(
                    messages=[
                        {"role": "system", "content": (
                            "You are an expert prompt engineer specialising in Azure OpenAI models. "
                            "You create high-quality system prompts that follow each model family's best practices. "
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

                    # Validate classification expected_category
                    if data_type == "classification" and canonical_categories:
                        canonical_set = {c.lower().strip() for c in canonical_categories}
                        invalid_count = 0
                        for sc in scenarios:
                            ec = sc.get("expected_category", "")
                            if isinstance(ec, str) and ec.lower().strip() not in canonical_set:
                                ec_lower = ec.lower().strip()
                                match = next(
                                    (c for c in canonical_categories
                                     if c.lower() in ec_lower or ec_lower in c.lower()),
                                    None,
                                )
                                if match:
                                    sc["expected_category"] = match
                                else:
                                    invalid_count += 1
                        if invalid_count:
                            logger.warning(
                                f"[!] {invalid_count}/{len(scenarios)} {data_type} "
                                f"scenarios have invalid expected_category values"
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
        data_dir: str = "data/synthetic",
    ) -> Dict[str, Any]:
        """
        Use an AI model to generate optimised prompts **and** matching
        synthetic test data for a given topic.

        Uses **parallel async calls** to speed up generation:
          - Step 1: gpt4/classification (must go first for category extraction)
          - Step 2: gpt5/classification + 6 other prompts (all in parallel)
          - Step 3: category alignment & auto-fix (if needed)
          - Step 4: all 5 data types in parallel

        Returns dict like:
            {
              "prompts": { "gpt4": { … }, "gpt5": { … } },
              "data": {
                "classification": { "count": 20, "file": "…" },
                "dialog":         { "count": 15, "file": "…" },
                "general":        { "count": 15, "file": "…" }
              }
            }
        """
        overall_t0 = time.time()
        result: Dict[str, Any] = {"prompts": {"gpt4": {}, "gpt5": {}}, "data": {}}

        # ── 0. Archive current topic before overwriting ───────────────
        current = self.get_topic_metadata().get("topic", "")
        if current and _slugify(current) != _slugify(topic):
            self.archive_current_topic()
            logger.info(f"Archived previous topic '{current}' before generating '{topic}'")

        # ── 1. Generate GPT-4 classification FIRST (needed for categories) ──
        logger.info("== Step 1/4: Generating gpt4/classification prompt (blocking) ==")
        reference = self.get_active_prompt("gpt4", "classification_agent_system") or ""
        reference_snippet = reference[:2000] if reference else "(no existing prompt)"

        gpt4_cls_result = self._run_async(
            self._async_generate_one_prompt(
                client, generator_model, topic,
                "gpt4", "classification", reference_snippet, None,
                asyncio.Semaphore(_MAX_CONCURRENT_LLM),
            )
        )
        _, _, gpt4_cls_content = gpt4_cls_result
        if not gpt4_cls_content.startswith("[Error"):
            self.save_prompt(
                model="gpt4", prompt_type="classification_agent_system",
                content=gpt4_cls_content, topic=topic,
                source="ai-generated", author=f"generator:{generator_model}",
            )
        result["prompts"]["gpt4"]["classification_agent_system"] = gpt4_cls_content

        # Extract categories from GPT-4 classification
        shared_categories: Optional[List[str]] = None
        if not gpt4_cls_content.startswith("[Error"):
            extracted = _extract_categories_from_prompt(gpt4_cls_content)
            if extracted:
                shared_categories = extracted
                logger.info(
                    f"Extracted {len(extracted)} categories from GPT-4 "
                    f"classification prompt: {extracted}"
                )

        # ── 2. Generate remaining 7 prompts in PARALLEL ───────────────
        logger.info("== Step 2/4: Generating 7 remaining prompts in parallel ==")
        parallel_tasks_step2 = []

        async def _step2():
            sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
            tasks = []
            for target_model in ("gpt4", "gpt5"):
                for task in ("classification", "dialog", "rag", "tool_calling"):
                    # Skip gpt4/classification — already done
                    if target_model == "gpt4" and task == "classification":
                        continue

                    prompt_type = f"{task}_agent_system"

                    # For gpt5/classification, use gpt4 cls as reference
                    if target_model == "gpt5" and task == "classification" and shared_categories:
                        ref_snippet = gpt4_cls_content if not gpt4_cls_content.startswith("[Error") else "(no reference)"
                    else:
                        ref = self.get_active_prompt(target_model, prompt_type) or ""
                        ref_snippet = ref[:2000] if ref else "(no existing prompt)"

                    cats = shared_categories if task == "classification" else None
                    tasks.append(
                        self._async_generate_one_prompt(
                            client, generator_model, topic,
                            target_model, task, ref_snippet, cats, sem,
                        )
                    )
            return await asyncio.gather(*tasks, return_exceptions=True)

        step2_results = self._run_async(_step2())

        # Process step 2 results — save prompts to disk
        for item in step2_results:
            if isinstance(item, Exception):
                logger.error(f"[parallel] Prompt generation exception: {item}")
                continue
            target_model, prompt_type, generated = item
            if not generated.startswith("[Error"):
                self.save_prompt(
                    model=target_model, prompt_type=prompt_type,
                    content=generated, topic=topic,
                    source="ai-generated", author=f"generator:{generator_model}",
                )
            result["prompts"][target_model][prompt_type] = generated

        # ── 1a. NOW update metadata (prompts are on disk) ─────────
        self._save_topic_metadata(topic, prompts_updated=True)

        # ── 3. Category alignment & auto-fix ──────────────────────────
        logger.info("== Step 3/4: Category alignment ==")
        _categories: Dict[str, List[str]] = {}
        for m in ("gpt5", "gpt4"):
            cls_prompt = result["prompts"].get(m, {}).get("classification_agent_system", "")
            if cls_prompt and not cls_prompt.startswith("[Error"):
                cats = _extract_categories_from_prompt(cls_prompt)
                if cats:
                    _categories[m] = cats
                    logger.info(f"Extracted {len(cats)} categories from {m} classification prompt: {cats}")

        canonical_categories: List[str] = []
        if "gpt5" in _categories and "gpt4" in _categories:
            set5 = set(_categories["gpt5"])
            set4 = set(_categories["gpt4"])
            common = [c for c in _categories["gpt4"] if c in set5]
            if len(common) >= len(set4) * 0.5:
                canonical_categories = common
                logger.info(
                    f"Using INTERSECTION of GPT-4 and GPT-5 categories "
                    f"({len(common)}/{len(set5 | set4)}): {common}"
                )
            else:
                canonical_categories = _categories["gpt4"]
                logger.warning(
                    f"Low category overlap ({len(common)}) between GPT-4 "
                    f"({list(set4)}) and GPT-5 ({list(set5)}) — "
                    f"using GPT-4 categories as source of truth"
                )
                # Auto-fix GPT-5 classification (single sync call)
                try:
                    logger.info("Auto-regenerating GPT-5 classification prompt to align taxonomy...")
                    gpt5_cls = result["prompts"].get("gpt5", {}).get("classification_agent_system", "")
                    fix_prompt = (
                        "The following system prompt was generated for a GPT-5 classification agent, "
                        "but it uses WRONG category codes. Rewrite it so that the EXACT primary "
                        "category codes listed below are used AS-IS (copy verbatim, do NOT rename).\n\n"
                        "MANDATORY PRIMARY CATEGORY CODES (use these EXACTLY):\n"
                        + '\n'.join(f'  - {c}' for c in _categories["gpt4"])
                        + "\n\nOriginal prompt to fix:\n" + gpt5_cls[:6000]
                        + "\n\nReturn ONLY the corrected system prompt. Keep the same structure, "
                        "descriptions, and subcategory style — just replace ALL primary category "
                        "codes with the mandatory ones above."
                    )
                    fix_res = client.complete(
                        messages=[
                            {"role": "system", "content": "You are an expert prompt engineer. Fix the category codes as instructed. Output ONLY the corrected prompt."},
                            {"role": "user", "content": fix_prompt},
                        ],
                        model_name=generator_model,
                    )
                    fixed_gpt5 = fix_res.content.strip()
                    fixed_cats = _extract_categories_from_prompt(fixed_gpt5)
                    fixed_overlap = len(set(fixed_cats) & set4)
                    if fixed_overlap >= len(set4) * 0.5:
                        self.save_prompt(
                            model="gpt5", prompt_type="classification_agent_system",
                            content=fixed_gpt5, topic=topic,
                            source="ai-generated-fixed", author=f"generator:{generator_model}",
                        )
                        result["prompts"]["gpt5"]["classification_agent_system"] = fixed_gpt5
                        _categories["gpt5"] = fixed_cats
                        logger.info(f"Successfully realigned GPT-5 categories ({fixed_overlap}/{len(set4)} overlap)")
                    else:
                        logger.warning(f"GPT-5 taxonomy fix attempt still has low overlap ({fixed_overlap}) — keeping GPT-4 categories for data")
                except Exception as e:
                    logger.error(f"Failed to auto-fix GPT-5 taxonomy: {e}")

            # Deterministic validation
            gpt5_final = result["prompts"].get("gpt5", {}).get("classification_agent_system", "")
            if gpt5_final and not gpt5_final.startswith("[Error") and "gpt4" in _categories:
                gpt5_lower = gpt5_final.lower()
                missing = [c for c in _categories["gpt4"] if c.lower() not in gpt5_lower]
                if missing:
                    logger.warning(
                        f"[!] DETERMINISTIC CHECK: {len(missing)} GPT-4 categories "
                        f"NOT found in GPT-5 prompt text: {missing}. "
                        f"Classification evaluation for GPT-5 may show mismatches."
                    )
                else:
                    logger.info(
                        "[OK] Deterministic check passed: ALL GPT-4 category codes "
                        "appear in GPT-5 classification prompt."
                    )
        else:
            canonical_categories = _categories.get("gpt4") or _categories.get("gpt5") or []

        if canonical_categories:
            logger.info(f"Canonical categories for data generation: {canonical_categories}")
        else:
            logger.warning("Could not extract categories from classification prompts — data generator will invent its own")

        # ── 4. Generate ALL synthetic test data in PARALLEL ───────────
        logger.info("== Step 4/4: Generating 5 data types in parallel ==")
        data_path = Path(data_dir)
        data_generators = [
            ("classification", self._build_classification_data_prompt, 20),
            ("dialog",         self._build_dialog_data_prompt,         15),
            ("general",        self._build_general_data_prompt,        15),
            ("rag",            self._build_rag_data_prompt,            10),
            ("tool_calling",   self._build_tool_calling_data_prompt,   10),
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
        self._save_topic_metadata(topic, data_generated=True)

        # Archive the newly generated topic so it's recoverable
        self.archive_current_topic()

        elapsed = time.time() - overall_t0
        logger.info(f"== Generation complete in {elapsed:.1f}s (parallel pipeline) ==")

        return result

    # ── Meta-prompt builders for prompts ──────────────────────────────

    def _build_generation_prompt(
        self, topic: str, target_model: str, task: str, reference_snippet: str,
        shared_categories: Optional[List[str]] = None,
    ) -> str:
        """Build the meta-prompt that instructs the AI to generate a system prompt."""

        model_guidance = {
            "gpt4": (
                "GPT-4.x best practices:\n"
                "- Use explicit Chain-of-Thought instructions\n"
                "- Provide detailed formatting rules and examples\n"
                "- Use Markdown tables for taxonomies\n"
                "- Be verbose with edge-case handling\n"
                "- Include concrete JSON output examples\n"
                "- Specify temperature=0.1 and seed for reproducibility"
            ),
            "gpt5": (
                "GPT-5.x best practices:\n"
                "- Leverage native reasoning (no explicit CoT needed)\n"
                "- Use YAML-based schema definitions for structure\n"
                "- Streamlined, concise instructions\n"
                "- Use <system_configuration> blocks for model params\n"
                "- Specify reasoning_effort level\n"
                "- Focus on WHAT not HOW — the model figures out the approach\n"
                "- Use max_completion_tokens instead of max_tokens"
            ),
        }

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
                "3. Adapt subcategories, descriptions, examples, and formatting "
                f"to the {target_model.upper()} style guidelines above\n"
                "4. Keep the same structural quality as the reference\n"
                "5. Output ONLY the system prompt content — no wrapper, no explanation"
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

        return f"""Generate a production-ready system prompt for the following scenario:

## TOPIC
{topic}

## TARGET MODEL
{target_model.upper()} — follow these guidelines:
{model_guidance.get(target_model, '')}

## TASK TYPE
Create {task_description.get(task, task)}

{ref_header}
{reference_snippet}

{requirements}
{self._shared_categories_block(shared_categories)}
"""

    @staticmethod
    def _shared_categories_block(categories: Optional[List[str]]) -> str:
        """Return an extra requirement block forcing the prompt to reuse given categories."""
        if not categories:
            return ""
        cat_list = '\n'.join(f'  - {c}' for c in categories)
        return (
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

    # ── Meta-prompt builders for synthetic data ───────────────────────

    def _build_classification_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating classification test scenarios."""

        if categories:
            cat_list = ', '.join(f'"{c}"' for c in categories)
            category_block = f"""MANDATORY CATEGORIES — use EXACTLY these category codes (copy them verbatim):
  [{cat_list}]
- You MUST use these exact strings as `expected_category` values — do NOT paraphrase, rename, or invent new ones.
- Each category must have 2-6 subcategories, also in readable snake_case.
- Distribute the {count} scenarios across ALL categories (at least 2 per category)."""
        else:
            category_block = f"""CATEGORY NAMING RULES:
- Invent 5-7 categories that are SPECIFIC and NATURAL for the topic "{topic}"
- Use descriptive snake_case names (e.g. "billing_inquiry", "technical_support",
  "travel_packages", "flight_operations", "safety_compliance")
- Do NOT use short acronyms or codes (no "BILL", "PKG", "TECH", etc.)
- Each category must have 2-6 subcategories, also in readable snake_case
- Categories must cover the full breadth of the domain"""

        return f"""Generate exactly {count} realistic classification test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema:
{{
  "scenarios": [
    {{
      "id": "CLASS_001",
      "scenario": "short_snake_case",
      "customer_input": "Realistic customer message (2-4 sentences)",
      "expected_category": "readable_snake_case",
      "expected_subcategory": "descriptive_subcategory",
      "expected_priority": "low|medium|high|critical",
      "expected_sentiment": "VALUE",
      "context": {{}},
      "follow_up_questions_expected": ["question1", "question2"]
    }}
  ]
}}

{category_block}

SENTIMENT VALUES (use exactly one of these):
  very_angry, angry, frustrated, concerned, worried, neutral, curious, cautious, positive, professional

IMPORTANT RULES:
1. ALL scenarios, customer inputs, categories, subcategories, and contexts must be domain-specific to "{topic}"
2. Use at least 5 of your invented categories, distributing scenarios across them
3. Distribute priorities evenly: ~20% critical, ~30% high, ~30% medium, ~20% low
4. Mix sentiments realistically using ONLY the values listed above
5. Customer inputs must be natural, varied in tone and length
6. Context fields should contain domain-specific metadata
7. Follow-up questions should be relevant and specific to the scenario
8. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_dialog_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating dialog test scenarios."""

        if categories:
            cat_list = ', '.join(f'"{c}"' for c in categories)
            category_block = f"""MANDATORY CATEGORIES — use ONLY these exact category codes:
  [{cat_list}]
Copy them VERBATIM as the `category` field value. Do NOT rename or invent new ones.
Distribute scenarios across at least 4 of these categories."""
        else:
            category_block = """CATEGORY NAMING: Use descriptive snake_case names specific to the topic
(e.g. "technical_support", "safety_compliance"). Do NOT use short codes or acronyms."""

        return f"""Generate exactly {count} realistic dialog/follow-up test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema:
{{
  "scenarios": [
    {{
      "id": "DLG_001",
      "scenario": "short_snake_case_name",
      "conversation": [
        {{"role": "customer", "message": "..."}},
        {{"role": "agent",    "message": "..."}},
        {{"role": "customer", "message": "..."}}
      ],
      "context_gaps": ["gap1", "gap2"],
      "optimal_follow_up": "Best next agent response (detailed)",
      "follow_up_rules": ["rule1", "rule2"],
      "expected_resolution_turns": 2,
      "category": "readable_snake_case"
    }}
  ]
}}

{category_block}

IMPORTANT RULES:
1. ALL scenarios, conversations, and categories must be domain-specific to "{topic}"
2. Vary conversation lengths: some with 1 turn, some with 2-3 turns
3. Context gaps should reflect realistic missing information for the domain
4. Optimal follow-ups should be professional, empathetic, and specific
5. Create at least 4 distinct categories
6. Mix simple and complex dialog situations
7. Customer messages should be natural and varied
8. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_general_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating general capability tests."""
        # General tests don't use the category taxonomy, but we accept
        # the kwarg for a uniform call signature.
        return f"""Generate exactly {count} general capability test cases for evaluating AI models on the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element uses one of these two variants:

Variant A — Single-prompt test:
{{
  "scenarios": [
    {{
      "id": "GEN_001",
      "test_type": "reasoning_capability|instruction_following|structured_output|consistency|edge_case_handling|multi_language|safety_boundary|summarization|calculation_accuracy|persona_adherence|ambiguity_resolution|compliance_awareness|negative_sentiment_handling",
      "prompt": "The test prompt",
      "complexity": "low|medium|high",
      "expected_output": null,
      "expected_behavior": "Description of correct behavior"
    }}
  ]
}}

Variant B — Multi-turn test:
{{
  "scenarios": [
    {{
      "id": "GEN_008",
      "test_type": "context_retention",
      "multi_turn": true,
      "conversation": [
        {{"role": "user", "content": "..."}},
        {{"role": "assistant", "content": "..."}},
        {{"role": "user", "content": "..."}}
      ],
      "expected_behavior": "...",
      "complexity": "medium"
    }}
  ]
}}

IMPORTANT RULES:
1. ALL test content must be domain-specific to "{topic}" — adapt scenarios, numbers, terminology
2. Use at least 6 different test_types across the {count} items
3. Distribute complexity: ~30% low, ~45% medium, ~25% high
4. Include at least 1 structured_output test with expected JSON
5. Include at least 1 consistency test with "run_count": 5
6. Include at least 1 safety_boundary test relevant to the domain
7. Include at least 1 multi_language test
8. Include at least 1 calculation_accuracy test with "expected_calculation" object
9. All prompts should be realistic and testable
10. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation, no comments inside the JSON
"""

    def _build_rag_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating RAG test scenarios."""
        # RAG tests don't use the category taxonomy, but we accept
        # the kwarg for a uniform call signature.
        return f"""Generate exactly {count} RAG (Retrieval-Augmented Generation) test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema:
{{
  "scenarios": [
    {{
      "id": "RAG_001",
      "scenario": "short_snake_case_name",
      "query": "A realistic user question about the topic",
      "context": "One or more paragraphs of retrieved context that contain relevant information. This should be 2-5 sentences of realistic domain text.",
      "ground_truth": "The correct answer that can be fully derived from the context.",
      "expected_behavior": "Description of how the model should use the context to answer.",
      "complexity": "low|medium|high"
    }}
  ]
}}

IMPORTANT RULES:
1. ALL scenarios must be domain-specific to "{topic}"
2. Context passages must be realistic and contain enough detail to answer the query
3. Ground truth must be FULLY derivable from the context (no external knowledge needed)
4. Include at least 1 scenario where context is INSUFFICIENT to fully answer the query
5. Include at least 1 scenario with CONTRADICTORY information in the context
6. Distribute complexity: ~30% low, ~40% medium, ~30% high
7. Queries should be natural and varied
8. Context should simulate real retrieved documents (policies, manuals, FAQs, articles)
9. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation
"""

    def _build_tool_calling_data_prompt(self, topic: str, count: int, *, categories: Optional[List[str]] = None) -> str:
        """Build the meta-prompt for generating tool-calling test scenarios."""
        # Tool calling tests don't use the category taxonomy.
        return f"""Generate exactly {count} tool-calling/function-calling test scenarios for the topic: "{topic}".

Return a JSON object with a single key "scenarios" containing an array.
Each element MUST have this exact schema:
{{
  "scenarios": [
    {{
      "id": "TC_001",
      "scenario": "short_snake_case_name",
      "query": "A natural user request that may or may not require tool usage",
      "available_tools": [
        {{
          "type": "function",
          "function": {{
            "name": "tool_name",
            "description": "What the tool does",
            "parameters": {{
              "type": "object",
              "properties": {{
                "param1": {{"type": "string", "description": "Param description"}}
              }},
              "required": ["param1"]
            }}
          }}
        }}
      ],
      "expected_tool_calls": ["tool_name"],
      "expected_parameters": {{
        "tool_name": {{"param1": "expected_value"}}
      }},
      "complexity": "low|medium|high"
    }}
  ]
}}

IMPORTANT RULES:
1. ALL scenarios, tools, and queries must be domain-specific to "{topic}"
2. Include 2-4 available tools per scenario (realistic for the domain)
3. Include at least 1 scenario where NO tool is needed (expected_tool_calls: [])
4. Include at least 1 scenario requiring MULTIPLE sequential tool calls
5. Include at least 1 scenario where required parameters are MISSING from the query
6. Include at least 1 scenario with AMBIGUOUS tool selection
7. Tool definitions should be realistic and well-structured
8. Parameter types should vary (string, number, boolean, array)
9. Distribute complexity: ~25% low, ~50% medium, ~25% high
10. Return ONLY the JSON object with "scenarios" key — no markdown fences, no explanation
"""
