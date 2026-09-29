"""Hold queues: members waiting for a book, first come first served."""
from dataclasses import dataclass
from datetime import date

from .models import NotFound


@dataclass
class Hold:
    isbn: str
    member_id: str
    placed: date


class HoldQueue:
    def __init__(self):
        self._queues = {}   # isbn -> list[Hold], oldest first

    def place(self, isbn, member_id, placed):
        queue = self._queues.setdefault(isbn, [])
        if any(h.member_id == member_id for h in queue):
            raise ValueError(f"{member_id} already holds {isbn}")
        hold = Hold(isbn, member_id, placed)
        queue.append(hold)
        return hold

    def cancel(self, isbn, member_id):
        queue = self._queues.get(isbn, [])
        for i, hold in enumerate(queue):
            if hold.member_id == member_id:
                del queue[i]
                return hold
        raise NotFound(f"no hold by {member_id} on {isbn}")

    def queue(self, isbn):
        """The holds on isbn, oldest first (a copy)."""
        return list(self._queues.get(isbn, []))

    def pop(self, isbn):
        """Remove and return the oldest hold on isbn, or None."""
        queue = self._queues.get(isbn)
        return queue.pop(0) if queue else None

    def all(self):
        return [h for isbn in sorted(self._queues) for h in self._queues[isbn]]
