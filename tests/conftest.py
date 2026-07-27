import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from bayan.coa_seed import seed_company
from bayan.db import make_session


@pytest.fixture
def session():
    s = make_session("sqlite:///:memory:")
    yield s
    s.close()


@pytest.fixture
def company(session):
    c = seed_company(session, "Тест ХХК")
    session.commit()
    return c
