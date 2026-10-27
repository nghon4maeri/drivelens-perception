"""`ActorContext` — ai đang chạy command (CONTRACTS §21.1). Module nhẹ, dùng chung cho api/store/worker.

Deploy pilot: `user_id` lấy từ token Supabase đã xác minh, actor `human:<user_id>`. Local v1: `user_id=None`, actor `human:local`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ActorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)

    @property
    def actor_string(self) -> str:
        return f"human:{self.user_id}" if self.user_id else "human:local"

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None
