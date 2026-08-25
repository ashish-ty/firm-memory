"""Coding-oriented memory taxonomy.

mem0's stock fact-extraction is tuned for consumer assistants (food, hobbies,
music), which produces near-useless tags for engineering work. Self-hosted OSS
has no project-scoped ``custom_categories``, but ``MemoryConfig.custom_instructions``
steers extraction — so the taxonomy is expressed as instructions instead.

Category names match ``integrations/mem0-plugin/scripts/setup_coding_categories.py``
so memory written by the editor plugin and by application code stays queryable
through one vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Category:
    """One memory type, with the description that drives extraction."""

    name: str
    description: str


CODING_CATEGORIES: tuple[Category, ...] = (
    Category(
        "architecture_decisions",
        "Design choices, system structure, technology selection, trade-offs evaluated, "
        "and architectural patterns adopted.",
    ),
    Category(
        "anti_patterns",
        "Approaches that failed, debugging dead-ends, and lessons learned from things that did not work.",
    ),
    Category(
        "task_learnings",
        "Strategies that succeeded for specific tasks, including tooling tricks and workflow shortcuts.",
    ),
    Category(
        "tooling_setup",
        "Development environment, build tools, dependencies, package managers, and deploy pipelines.",
    ),
    Category(
        "bug_fixes",
        "Bug fixes with root cause, the fix applied, and how it was diagnosed.",
    ),
    Category(
        "coding_conventions",
        "Code style, naming, file organisation, error-handling conventions, and team agreements.",
    ),
    Category(
        "dependency_decisions",
        "Why libraries, frameworks, or versions were chosen or replaced, and the alternatives considered.",
    ),
    Category(
        "performance_findings",
        "Profiling results, bottlenecks, optimisations applied, and measurable improvements.",
    ),
    Category(
        "review_feedback",
        "Recurring review comments and agreed resolutions, including rejected approaches and why.",
    ),
)

#: Memory is for durable judgements, not a document store. Without these
#: exclusions transcripts flood the pool and drown the signal.
_EXCLUSIONS = (
    "Do NOT store source code bodies, file contents, diffs, or stack traces — store the conclusion drawn from them.",
    "Do NOT store secrets, credentials, tokens, connection strings, or personal data.",
    "Do NOT store facts about individual engineers or teams (who prefers what, who works how) — "
    "there is no per-person layer, so such a fact would be applied to everyone. Store the "
    "convention the codebase follows, not the person who asked for it.",
    "Do NOT store transient state (what a command printed, which file was open, current line numbers).",
    "Do NOT store restatements of the request; store only what stays true after the task ends.",
)


def fact_extraction_instructions(categories: tuple[Category, ...] = CODING_CATEGORIES) -> str:
    """Render ``MemoryConfig.custom_instructions`` for engineering memory."""
    category_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    exclusion_lines = "\n".join(f"- {rule}" for rule in _EXCLUSIONS)
    return (
        "You are extracting durable engineering memory for a software firm. Retain only "
        "facts that remain useful weeks later, on a different task, in the same codebase.\n\n"
        f"Classify each retained fact into exactly one of these categories:\n{category_lines}\n\n"
        f"Exclusions:\n{exclusion_lines}\n\n"
        "Prefer one specific, self-contained sentence per fact, including the reason a choice "
        "was made. A fact that cannot be attributed to a category should be discarded."
    )
