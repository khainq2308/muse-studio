from __future__ import annotations

from typing_extensions import TypeAliasType


# Reusable recursive JSON shape for extensible provider and redacted payload data.
JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)
