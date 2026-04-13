from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ExecuteDelta:
    changed_paths: list[str]
    dirty_unknown: bool


def mark_execute_dirty_unknown(state: dict, exit_code: int) -> dict:
    if exit_code == 0:
        state["dirty_unknown"] = True
    return state


def parse_name_status_diff(diff_output: str) -> list[str]:
    paths: list[str] = []
    for line in diff_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            paths.append(parts[-1].strip())
    return paths

