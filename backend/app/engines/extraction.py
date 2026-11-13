"""Endpoint snapshot extraction with injectable partial failures.

Extraction pulls one endpoint's rows into a snapshot. A failure can be injected
after N rows so the pipeline must decide what to do with the half-written
snapshot and the already successful side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SIDES = ("left", "right")


@dataclass
class SideExtraction:
    side: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    expected_rows: int = 0
    extracted_rows: int = 0
    failed: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "expected_rows": self.expected_rows,
            "extracted_rows": self.extracted_rows,
            "failed": self.failed,
            "reason": self.reason,
        }


def extract_side(
    fixture_rows: list[dict[str, Any]],
    *,
    side: str,
    fail_after: int | None = None,
) -> SideExtraction:
    """Extract a side; ``fail_after`` injects a connection drop after N rows.

    ``fail_after >= expected`` means the injected fault never fires, so the
    extraction is a complete success.
    """
    if side not in SIDES:
        raise ValueError(f"unknown extraction side: {side}")
    expected = len(fixture_rows)
    if fail_after is None or fail_after >= expected:
        return SideExtraction(
            side=side,
            rows=list(fixture_rows),
            expected_rows=expected,
            extracted_rows=expected,
            failed=False,
        )
    n = max(0, int(fail_after))
    reason = (
        f"注入故障：{side} 端抽取在读取第 {n}/{expected} 行后连接中断，"
        f"只拿到半截快照（{n} 行）"
    )
    return SideExtraction(
        side=side,
        rows=list(fixture_rows[:n]),
        expected_rows=expected,
        extracted_rows=n,
        failed=True,
        reason=reason,
    )
