"""
Category Parser — extract top-level category codes from prompt text.

Factored out of ``prompt_manager.py`` (A2 refactor) so that:
 - ``evaluator.py`` can import it without pulling in the full PromptManager,
 - the parser can be tested in isolation, and
 - ``prompt_manager.py`` stays focused on versioning / generation.

Re-exported from ``prompt_manager`` for backward compatibility.
"""

import re
from typing import List, Optional

__all__ = ["extract_categories_from_prompt"]


def extract_categories_from_prompt(prompt_text: str) -> List[str]:
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

    # Helper: detect whether a line is a taxonomy-section heading.
    # Supports both Markdown headings (starting with #) and plain-text
    # ALL-CAPS headings like ``TELCO TAXONOMY (CATEGORIES AND SUBCATEGORIES)``.
    _TAXONOMY_KW_RE = re.compile(r'(categor|taxonom|classif)', re.IGNORECASE)
    _EXCLUSION_KW_RE = re.compile(
        r'(priorid|priority|sentim|output|format|ejemplo|example|instruc|entity|risk|follow)',
        re.IGNORECASE)
    _SUB_ONLY_RE = re.compile(r'(secondary|sub\s*categor)', re.IGNORECASE)
    _SUB_EXCEPTION_RE = re.compile(
        r'(taxonom|categor[ií](as?|es)\s+(y|and|e|principal)|primary\s+categor)',
        re.IGNORECASE)
    # Plain-text heading: ALL-CAPS line (≥ 60 % uppercase letters,
    # at least 10 chars, no leading ``-``/``*`` bullets).
    # Includes accented uppercase (ÁÉÍÓÚÑÜ) and common symbols (->:.).
    _ALLCAPS_HEADING_RE = re.compile(
        r'^[A-Z\u00C0-\u00DD]'
        r'[A-Z\u00C0-\u00DD0-9 _()&,/—–>.<:;\-]{9,}$'
    )

    def _is_heading(stripped: str) -> bool:
        """Return True if the line looks like a section heading."""
        if stripped.startswith('#'):
            return True
        # Plain-text ALLCAPS heading (common in LLM-generated prompts)
        if _ALLCAPS_HEADING_RE.match(stripped):
            return True
        # Heuristic fallback: line with ≥ 60 % uppercase letters,
        # at least 10 chars, no leading bullet — catches headings
        # with accented characters or symbols not in the regex.
        if len(stripped) >= 10 and stripped[0] not in '-*|':
            alpha = [c for c in stripped if c.isalpha()]
            if len(alpha) >= 5 and sum(1 for c in alpha if c.isupper()) / len(alpha) >= 0.6:
                return True
        # Heuristic 2: ALLCAPS prefix before parenthetical qualifier
        # e.g. "TAXONOMÍA PERMITIDA (primary_category y subcategory)"
        # or   "TAXONOMÍA (primary_category -> subcategories)"
        if '(' in stripped and stripped[0] not in '-*|':
            prefix = stripped.split('(')[0].strip()
            prefix_alpha = [c for c in prefix if c.isalpha()]
            if len(prefix_alpha) >= 5 and all(c.isupper() for c in prefix_alpha):
                return True
        return False

    # ── Strategy 1: Markdown tables ───────────────────────────────
    # Look for tables inside sections whose heading mentions categories
    # / taxonomy.  Detect the code column flexibly.
    in_taxonomy_section = False
    in_table = False
    cat_col_idx: Optional[int] = None
    separator_seen = False

    for line in lines:
        stripped = line.strip()

        # Detect section headings (# Markdown OR ALLCAPS plain text)
        if _is_heading(stripped):
            heading_lower = stripped.lower()
            is_excluded = bool(_EXCLUSION_KW_RE.search(heading_lower))
            is_sub_only = bool(_SUB_ONLY_RE.search(heading_lower)) \
                and not _SUB_EXCEPTION_RE.search(heading_lower)
            if is_excluded or is_sub_only:
                in_taxonomy_section = False
            elif _TAXONOMY_KW_RE.search(heading_lower):
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
            # Skip tables whose header explicitly labels columns as
            # subcategory data — these are not primary category tables.
            if any(re.search(r'sub.?categor', col) for col in cols):
                continue
            # Pass 1: look for a column explicitly named as a code column
            for i, col in enumerate(cols):
                if col in ('code', 'category_code', 'codigo', 'código',
                           'category code', 'código de categoría'):
                    cat_col_idx = i
                    in_table = True
                    break
            # Pass 2: "categoría principal", "primary category", etc.
            if not in_table:
                for i, col in enumerate(cols):
                    if re.search(r'categor[ií]a.*principal|primary.*categ|category\s*(code|cod)', col):
                        cat_col_idx = i
                        in_table = True
                        break
            # Pass 3: "Categoría principal (category)" style headers
            if not in_table:
                for i, col in enumerate(cols):
                    if re.search(r'categor[ií]a.*\(', col):
                        cat_col_idx = i
                        in_table = True
                        break
            # If no explicit column found, tentatively enter table mode
            if not in_table and len([c for c in cols if c.strip()]) >= 2:
                in_table = True
                cat_col_idx = None
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
    if not categories:
        in_tax = False
        list_indent: Optional[int] = None
        for line in lines:
            stripped = line.strip()
            if _is_heading(stripped):
                heading_lower = stripped.lower()
                is_excluded = bool(_EXCLUSION_KW_RE.search(heading_lower))
                is_sub_only = bool(_SUB_ONLY_RE.search(heading_lower)) \
                    and not _SUB_EXCEPTION_RE.search(heading_lower)
                if is_excluded or is_sub_only:
                    in_tax = False
                elif _TAXONOMY_KW_RE.search(heading_lower):
                    in_tax = True
                    list_indent = None
                continue
            if not in_tax:
                continue
            m = re.match(r'^(\s*)(?:[-*]|\d+[).])\s+([a-z][a-z0-9_]{2,}):?\s*$', line)
            if m:
                indent = len(m.group(1))
                if list_indent is None:
                    list_indent = indent
                if indent == list_indent:
                    _add(m.group(2))
            m2 = re.match(r'^\s*[-*]\s+\**([a-z][a-z0-9_]{2,})\**\s*$', line)
            if m2 and list_indent is not None:
                pass  # already handled above

    # ── Strategy 4: Sub-heading category codes ────────────────────
    if not categories:
        _SUBHEADING_CODE_RE = re.compile(
            r'^#{2,4}\s+'
            r'(?:\d[\d.\)]*\s+)?'
            r'([a-z][a-z0-9_]{2,})'
            r'(?:\s*\(.*\))?'
            r'\s*$'
        )
        in_tax = False
        for line in lines:
            stripped = line.strip()
            if _is_heading(stripped):
                heading_lower = stripped.lower()
                is_excluded = bool(_EXCLUSION_KW_RE.search(heading_lower))
                is_sub_only = bool(_SUB_ONLY_RE.search(heading_lower)) \
                    and not _SUB_EXCEPTION_RE.search(heading_lower)
                if is_excluded or is_sub_only:
                    in_tax = False
                elif _TAXONOMY_KW_RE.search(heading_lower):
                    in_tax = True
                if in_tax and stripped.startswith('#'):
                    m = _SUBHEADING_CODE_RE.match(stripped)
                    if m:
                        _add(m.group(1))
                continue
            if not in_tax:
                continue

    # ── Strategy 5: Bare snake_case lines under a taxonomy heading ─
    if not categories:
        in_tax = False
        for line in lines:
            stripped = line.strip()
            if _is_heading(stripped):
                heading_lower = stripped.lower()
                is_excluded = bool(_EXCLUSION_KW_RE.search(heading_lower))
                is_sub_only = bool(_SUB_ONLY_RE.search(heading_lower)) \
                    and not _SUB_EXCEPTION_RE.search(heading_lower)
                if is_excluded or is_sub_only:
                    in_tax = False
                elif _TAXONOMY_KW_RE.search(heading_lower):
                    in_tax = True
                continue
            if not in_tax:
                continue
            if not stripped or ' ' in stripped:
                if not stripped:
                    continue
                if categories:
                    break
                continue
            if _CODE_RE.match(stripped):
                _add(stripped)

    return categories


# Backward-compatible alias (underscore-prefixed name used by old callers)
_extract_categories_from_prompt = extract_categories_from_prompt
