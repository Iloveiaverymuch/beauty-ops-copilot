"""Small-sample statistics so differences are reported with their uncertainty, not as bare percentages.

- Wilson interval: 95% CI for a proportion (correct at small n, unlike +-1.96*sqrt(p(1-p)/n)).
- McNemar exact test: two modes answered the SAME questions; only the questions where they DISAGREE carry
  information (b = A right/B wrong, c = A wrong/B right). p-value = two-sided binomial test of b vs c at 0.5.
"""
from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def mcnemar(a: list[bool], b: list[bool]) -> tuple[int, int, float]:
    """Returns (A-only wins, B-only wins, exact two-sided p)."""
    x = sum(1 for i, j in zip(a, b) if i and not j)
    y = sum(1 for i, j in zip(a, b) if j and not i)
    n = x + y
    if n == 0:
        return x, y, 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(x, y) + 1)) / 2 ** n
    return x, y, min(1.0, 2 * tail)
