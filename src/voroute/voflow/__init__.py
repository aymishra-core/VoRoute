"""Call orchestration: decides when to place a call and queues it."""

from voroute.voflow.job import (
    CapturePath,
    ConfirmationJob,
    InvalidJobTransition,
    JobStatus,
)
from voroute.voflow.script import build_closing, build_script

__all__ = [
    "CapturePath",
    "ConfirmationJob",
    "InvalidJobTransition",
    "JobStatus",
    "build_closing",
    "build_script",
    "enqueue",
]


def __getattr__(name: str) -> object:
    if name == "enqueue":
        from voroute.voflow.dispatcher import enqueue

        return enqueue
    raise AttributeError(name)
