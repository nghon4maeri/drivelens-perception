"""Class `Command` + danh sách đăng ký `COMMANDS` (CONTRACTS §11.1).

REST route (L6) và tool schema của agent đều sinh từ `COMMANDS`, nên UI và agent không lệch nhau.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ..service import App


class Command:
    name: ClassVar[str]
    description: ClassVar[str]          # agent đọc để biết khi nào dùng
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel]]
    mutates: ClassVar[bool] = False
    needs_confirm: ClassVar[bool] = False

    def run(self, inp: BaseModel, app: App) -> BaseModel:
        raise NotImplementedError


COMMANDS: dict[str, Command] = {}


def command(cls: type[Command]) -> type[Command]:
    if cls.name in COMMANDS:
        raise ValueError(f"trùng tên command {cls.name!r}")
    COMMANDS[cls.name] = cls()
    return cls
