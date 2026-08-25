"""The firm's engineering memory taxonomy.

A provider's stock fact-extraction is tuned for consumer assistants (food,
hobbies, music), which produces near-useless tags for engineering work. This
module is where the firm overrides that: it fixes the vocabulary, the
descriptions that drive extraction, and — just as importantly — the exclusions
that keep the pool full of judgements rather than transcripts.

Taxonomy is a **firm-owned** concern, enforced before anything reaches a
provider (HLD §5). Enum *values* are the wire format and are deliberately
unchanged from the pre-platform vocabulary, so memory already written by the
editor plugin (``integrations/mem0-plugin/scripts/setup_coding_categories.py``)
and by application code stays queryable through one vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import TaxonomyError


class MemoryType(StrEnum):
    """Every kind of durable engineering knowledge the firm stores.

    Member names follow the platform design's taxonomy; values are the stable
    on-the-wire category slugs.
    """

    # --- Carried over from the original coding taxonomy (values unchanged) ---
    ARCHITECTURE_DECISION = "architecture_decisions"
    REJECTED_APPROACH = "anti_patterns"
    CONVENTION = "coding_conventions"
    REVIEW_PATTERN = "review_feedback"
    BUG_FIX = "bug_fixes"
    TASK_LEARNING = "task_learnings"
    TOOLING_SETUP = "tooling_setup"
    DEPENDENCY_DECISION = "dependency_decisions"
    PERFORMANCE_FINDING = "performance_findings"

    # --- Added by the Firm Memory Platform design ---
    BUSINESS_RULE = "business_rules"
    PRODUCTION_ISSUE = "production_issues"
    OWNERSHIP = "ownership"
    TERMINOLOGY = "terminology"


@dataclass(frozen=True, slots=True)
class Category:
    """One memory type, with the description that drives extraction."""

    type: MemoryType
    description: str

    @property
    def name(self) -> str:
        """The stable category slug used in storage and filters."""
        return self.type.value


CODING_CATEGORIES: tuple[Category, ...] = (
    Category(
        MemoryType.ARCHITECTURE_DECISION,
        "Design choices, system structure, technology selection, trade-offs evaluated, "
        "and architectural patterns adopted.",
    ),
    Category(
        MemoryType.REJECTED_APPROACH,
        "Approaches that failed, debugging dead-ends, and lessons learned from things that did not work.",
    ),
    Category(
        MemoryType.TASK_LEARNING,
        "Strategies that succeeded for specific tasks, including tooling tricks and workflow shortcuts.",
    ),
    Category(
        MemoryType.TOOLING_SETUP,
        "Development environment, build tools, dependencies, package managers, and deploy pipelines.",
    ),
    Category(
        MemoryType.BUG_FIX,
        "Bug fixes with root cause, the fix applied, and how it was diagnosed.",
    ),
    Category(
        MemoryType.CONVENTION,
        "Code style, naming, file organisation, error-handling conventions, and team agreements.",
    ),
    Category(
        MemoryType.DEPENDENCY_DECISION,
        "Why libraries, frameworks, or versions were chosen or replaced, and the alternatives considered.",
    ),
    Category(
        MemoryType.PERFORMANCE_FINDING,
        "Profiling results, bottlenecks, optimisations applied, and measurable improvements.",
    ),
    Category(
        MemoryType.REVIEW_PATTERN,
        "Recurring review comments and agreed resolutions, including rejected approaches and why.",
    ),
    Category(
        MemoryType.BUSINESS_RULE,
        "Domain constraints the code must satisfy but does not explain — venue rules, cut-off times, "
        "routing and margin requirements, and the reason each exists.",
    ),
    Category(
        MemoryType.PRODUCTION_ISSUE,
        "Incidents and outages with symptom, root cause, and remediation, so a recurrence is recognised "
        "rather than re-diagnosed.",
    ),
    Category(
        MemoryType.OWNERSHIP,
        "Which component or service is responsible for a behaviour, and where a change of this kind belongs.",
    ),
    Category(
        MemoryType.TERMINOLOGY,
        "Firm-internal vocabulary, acronyms, and product names, with what each expands to and means here.",
    ),
)

#: Memory is for durable judgements, not a document store. Without these
#: exclusions transcripts flood the pool and drown the signal.
EXCLUSIONS: tuple[str, ...] = (
    "Do NOT store source code bodies, file contents, diffs, or stack traces — store the conclusion drawn from them.",
    "Do NOT store secrets, credentials, tokens, connection strings, or personal data.",
    "Do NOT store facts about individual engineers or teams (who prefers what, who works how) — "
    "there is no per-person layer, so such a fact would be applied to everyone. Store the "
    "convention the codebase follows, not the person who asked for it.",
    "Do NOT store transient state (what a command printed, which file was open, current line numbers).",
    "Do NOT store restatements of the request; store only what stays true after the task ends.",
)

_BY_VALUE = {category.type.value: category for category in CODING_CATEGORIES}
_BY_NAME = {category.type.name: category for category in CODING_CATEGORIES}


def coerce_type(value: MemoryType | str) -> MemoryType:
    """Resolve *value* to a :class:`MemoryType`, accepting either spelling.

    Both the wire slug (``"architecture_decisions"``) and the platform member
    name (``"ARCHITECTURE_DECISION"``) resolve, so agents and MCP callers are
    not forced to know which vocabulary a given document used.
    """
    if isinstance(value, MemoryType):
        return value

    key = (value or "").strip()
    if not key:
        raise TaxonomyError("A memory type is required; the taxonomy has no default")

    category = _BY_VALUE.get(key.lower()) or _BY_NAME.get(key.upper())
    if category is None:
        raise TaxonomyError(f"Unknown memory type {value!r}. Allowed: {', '.join(sorted(_BY_VALUE))}")
    return category.type


def fact_extraction_instructions(categories: tuple[Category, ...] = CODING_CATEGORIES) -> str:
    """Render the provider's fact-extraction instructions for engineering memory."""
    category_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    exclusion_lines = "\n".join(f"- {rule}" for rule in EXCLUSIONS)
    return (
        "You are extracting durable engineering memory for a software firm. Retain only "
        "facts that remain useful weeks later, on a different task, in the same codebase.\n\n"
        f"Classify each retained fact into exactly one of these categories:\n{category_lines}\n\n"
        f"Exclusions:\n{exclusion_lines}\n\n"
        "Prefer one specific, self-contained sentence per fact, including the reason a choice "
        "was made. A fact that cannot be attributed to a category should be discarded."
    )
