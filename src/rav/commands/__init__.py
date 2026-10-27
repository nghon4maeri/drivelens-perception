"""Command layer (CONTRACTS §11): mỗi hành động là một command có Pydantic Input/Output.
UI (qua REST) và agent sau này gọi cùng danh sách `COMMANDS`."""

from ..service import App, ServiceError
from . import (  # noqa: F401  (đăng ký command)
    batch_yolo,
    benchmark,
    cloud,
    datasets,
    evidence,
    explain,
    export,
    metrics,
    models,
    proposal_jobs,
    proposals,
    recipes,
    sessions,
    snapshots,
    state,
    videos,
    views,
)
from .base import COMMANDS, Command

__all__ = ["COMMANDS", "App", "Command", "ServiceError"]
