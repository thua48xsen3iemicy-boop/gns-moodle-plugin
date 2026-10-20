"""Task-local command authority. Receivers persist and enforce the same token."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .store import Fenced


@dataclass(frozen=True)
class Authority:
    epoch: int
    generation: int
    valid: object

    def headers(self):
        if not self.valid():
            raise Fenced("Manager no longer owns the command authority")
        return {"X-Lab-Epoch": str(self.epoch), "X-Lab-Generation": str(self.generation)}

    def body(self):
        self.headers()
        return {"epoch": self.epoch, "generation": self.generation}


current = ContextVar("lab_command_authority", default=None)


def command_headers():
    authority = current.get()
    if authority is None:
        raise Fenced("Mutating command requires an ownership scope")
    return authority.headers()


@contextmanager
def command_scope(epoch, generation, valid=lambda: True):
    token = current.set(Authority(epoch, generation, valid))
    try:
        yield current.get()
    finally:
        current.reset(token)
