"""
UserContext — resolves all filesystem paths for an authenticated user.

Every user gets an isolated directory tree under ``data/users/<user_id>/``
that mirrors the structure previously shared globally.
"""

import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class UserContext:
    """Encapsulates the per-user directory layout.

    Directory structure::

        data/users/<user_id>/
        ├── prompts/
        │   ├── gpt4/
        │   ├── gpt5/
        │   ├── history/
        │   └── topics/
        ├── synthetic/
        │   ├── classification/
        │   ├── dialog/
        │   ├── general/
        │   ├── rag/
        │   ├── tool_calling/
        │   └── topics/
        └── results/
    """

    _DATA_TYPES = ("classification", "dialog", "general", "rag", "tool_calling")

    def __init__(self, user_id: str, base_dir: str = "data/users"):
        if not user_id:
            raise ValueError("user_id must not be empty")
        self.user_id = user_id
        self.base = Path(base_dir) / user_id

    # ── Derived paths ────────────────────────────────────────────────

    @property
    def prompts_dir(self) -> Path:
        """Active prompts root  (was: ``prompts/``)"""
        return self.base / "prompts"

    @property
    def data_dir(self) -> Path:
        """Synthetic test data root  (was: ``data/synthetic/``)"""
        return self.base / "synthetic"

    @property
    def results_dir(self) -> Path:
        """Evaluation / comparison results  (was: ``data/results/``)"""
        return self.base / "results"

    @property
    def history_dir(self) -> Path:
        return self.prompts_dir / "history"

    @property
    def topics_dir(self) -> Path:
        return self.prompts_dir / "topics"

    @property
    def data_topics_dir(self) -> Path:
        return self.data_dir / "topics"

    # ── Bootstrap ────────────────────────────────────────────────────

    def ensure_dirs(self, model_keys: Optional[list] = None):
        """Create the full directory skeleton for a new user.

        If *model_keys* is provided (e.g. ``['gpt4', 'gpt5', 'gpt4o']``),
        a sub-directory is created under ``prompts/`` for each model.
        """
        for d in (
            self.prompts_dir,
            self.data_dir,
            self.results_dir,
            self.history_dir,
            self.topics_dir,
            self.data_topics_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        for t in self._DATA_TYPES:
            (self.data_dir / t).mkdir(exist_ok=True)

        if model_keys:
            for mk in model_keys:
                (self.prompts_dir / mk).mkdir(exist_ok=True)

        logger.info(f"User directory ensured: {self.base}")

    def seed_from_shared(self, shared_prompts: str = "prompts", shared_data: str = "data/synthetic"):
        """Copy shared/default prompts and data into the user's namespace.

        Only copies if the user's directories are empty (first login).
        Skips ``history/`` and ``topics/`` sub-dirs from the source.
        """
        prompts_src = Path(shared_prompts)
        data_src = Path(shared_data)

        # Copy prompts (model dirs only, skip history/topics)
        if prompts_src.exists():
            skip = {"history", "topics", "templates", "__pycache__"}
            for model_dir in prompts_src.iterdir():
                if model_dir.is_dir() and model_dir.name not in skip:
                    dest = self.prompts_dir / model_dir.name
                    # Check for actual prompt files (.md), not just any
                    # content — empty dirs created by ensure_dirs or
                    # leftover non-prompt files should not block seeding.
                    has_prompts = dest.exists() and any(dest.glob("*.md"))
                    if not has_prompts:
                        shutil.copytree(model_dir, dest, dirs_exist_ok=True)
                        logger.info(f"Seeded prompts for {model_dir.name}")

        # Copy synthetic data (type dirs only, skip topics)
        if data_src.exists():
            skip = {"topics", "__pycache__"}
            for type_dir in data_src.iterdir():
                if type_dir.is_dir() and type_dir.name not in skip:
                    dest = self.data_dir / type_dir.name
                    # Check for actual data files (.json), not just any content
                    has_data = dest.exists() and any(dest.glob("*.json"))
                    if not has_data:
                        shutil.copytree(type_dir, dest, dirs_exist_ok=True)
                        logger.info(f"Seeded data for {type_dir.name}")

        # Copy topic archives (prompts/topics/ and data/synthetic/topics/)
        # Only copy shared topics that do NOT already exist in the user's
        # directory — never overwrite user-created or user-imported topics.
        src_prompt_topics = prompts_src / "topics"
        if src_prompt_topics.exists():
            for topic_dir in src_prompt_topics.iterdir():
                if topic_dir.is_dir():
                    dest = self.topics_dir / topic_dir.name
                    if not dest.exists():
                        shutil.copytree(topic_dir, dest)
                        logger.info(f"Seeded prompt topic archive: {topic_dir.name}")

        src_data_topics = data_src / "topics"
        if src_data_topics.exists():
            for topic_dir in src_data_topics.iterdir():
                if topic_dir.is_dir():
                    dest = self.data_topics_dir / topic_dir.name
                    if not dest.exists():
                        shutil.copytree(topic_dir, dest)
                        logger.info(f"Seeded data topic archive: {topic_dir.name}")

        # Copy topic_metadata.json so the UI knows which topic is active.
        # Version history (versions.json) is intentionally NOT copied —
        # each user starts with a clean prompt-version history that only
        # tracks their own changes.
        # NEVER overwrite an existing topic_metadata.json — the user may
        # have switched topics or generated new ones since first login.
        src_topic_meta = prompts_src / "history" / "topic_metadata.json"
        if src_topic_meta.exists():
            dest_topic_meta = self.history_dir / "topic_metadata.json"
            if not dest_topic_meta.exists():
                shutil.copy2(src_topic_meta, dest_topic_meta)
                logger.info("Seeded topic_metadata.json from shared history")

        logger.info(f"Seeded user {self.user_id} from shared data")

    @property
    def is_initialised(self) -> bool:
        """True if the user directory already exists and has content."""
        return self.base.exists() and any(self.base.iterdir())
