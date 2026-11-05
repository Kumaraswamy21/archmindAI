"""In-process cancel signals for a live request. Lost on process restart."""

from threading import Event, Lock
from uuid import UUID

_lock = Lock()
_events: dict[UUID, Event] = {}


def register(workflow_id: UUID, event: Event) -> None:
    with _lock:
        _events[workflow_id] = event


def unregister(workflow_id: UUID) -> None:
    with _lock:
        _events.pop(workflow_id, None)


def signal(workflow_id: UUID) -> bool:
    with _lock:
        event = _events.get(workflow_id)
    if event is None:
        return False
    event.set()
    return True
