"""Keep the suite offline: enqueue always dials through a stub unless a test sets its own provider."""

import pytest

from voroute.config import settings
from voroute.db import prepare_database, reset_database
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


@pytest.fixture(autouse=True)
def sqlite_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/voroute.db")
    monkeypatch.setattr(settings, "voroute_api_key", "test-key")
    reset_database()
    prepare_database()
    yield
    reset_database()
