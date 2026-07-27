"""Context variables for storing request-scoped tenant and actor information.
"""

from __future__ import annotations

import contextvars

current_actor_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_actor_id", default=None)
current_company_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_company_id", default=None)
