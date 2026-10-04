"""Keep the suite offline: enqueue always dials through a stub unless a test sets its own provider."""

import pytest

from voroute.voflow.job import ConfirmationJob
from voroute.voline.provider import set_provider


class _StubProvider:
    def place_call(self, to: str, job: ConfirmationJob) -> str:
        return "CA_TEST"


@pytest.fixture(autouse=True)
def stub_telephony() -> object:
    set_provider(_StubProvider())
    yield
    set_provider(None)
