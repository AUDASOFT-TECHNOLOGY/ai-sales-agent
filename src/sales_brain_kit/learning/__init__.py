"""The agent learns from its results: what to measure, how to review, which rules to keep."""

from sales_brain_kit.learning.induction import consolidate
from sales_brain_kit.learning.practices import BY_CODE, CATALOG, Practice
from sales_brain_kit.learning.review import review_case

__all__ = ["BY_CODE", "CATALOG", "Practice", "consolidate", "review_case"]
