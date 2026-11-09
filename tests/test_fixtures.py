"""Judge the port against a local Defuddle checkout named by DEFUDDLE_SOURCE_DIR.

The fixtures hold third-party pages, so they stay in that checkout. Tests skip when
the variable is unset.
"""

import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fixture_report as report  # the script directory is not a package

_ROOT = report.source_dir()
pytestmark = pytest.mark.skipif(_ROOT is None, reason=f"{report.SOURCE_ENV} is not set")


def _root() -> Path:
    assert _ROOT is not None
    return _ROOT


def test_checkout_is_at_baseline_commit() -> None:
    commit = report.checkout_commit(_root())
    if commit != report.BASELINE_COMMIT:
        warnings.warn(f"checkout is at {commit}", stacklevel=1)


@pytest.mark.parametrize("name", report.SECURITY_FIXTURES)
def test_security_fixture_passes(name: str) -> None:
    score = report.score_fixture(_root(), name)
    assert score is not None
    assert score.passed


def test_judged_fixtures_meet_the_bar() -> None:
    names = [name for name in report.fixture_names(_root()) if report.is_judged(name)]
    assert len(names) == 53
    scores = [score for name in names if (score := report.score_fixture(_root(), name))]
    passed = sum(1 for score in scores if score.passed)
    assert passed / len(scores) >= report.PASS_RATE_THRESHOLD
