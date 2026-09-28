"""Ordered, short-circuit message routing without Discord or service dependencies."""
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Awaitable, Callable, Iterable, Optional


class Phase(IntEnum):
    CHANNEL = 10
    COMMAND = 20
    OBSERVER = 30
    BACKEND = 40
    LEGACY = 50


@dataclass(frozen=True)
class Route:
    name: str
    phase: Phase
    priority: int
    handler: Callable[[Any, Optional[str]], Awaitable[bool]]


class MessageRouter:
    """True means consumed (including rejection); False means try the next route.

    Exceptions propagate to Discord's event error handling as before. Registrations
    must have unique names and ordering keys, so ties never depend on file order.
    """

    def __init__(self, routes: Iterable[Route]):
        self.routes = tuple(sorted(routes, key=lambda route: (route.phase, route.priority)))
        if len({route.name for route in self.routes}) != len(self.routes):
            raise ValueError("duplicate message route name")
        if len({(route.phase, route.priority) for route in self.routes}) != len(self.routes):
            raise ValueError("duplicate message route phase/priority")

    async def dispatch(self, message: Any, command_text: Optional[str]) -> bool:
        for route in self.routes:
            handled = await route.handler(message, command_text)
            if type(handled) is not bool:
                raise TypeError(f"message route {route.name} must return bool")
            if handled:
                return True
        return False
