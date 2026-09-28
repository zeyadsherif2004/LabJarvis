from datetime import UTC, datetime

import pytest

from labjarvis.application.errors import ApplicationError
from labjarvis.application.session_orchestration import SessionOrchestrationService


def test_discovery_range_requires_timezone_aware_ordered_bounds() -> None:
    with pytest.raises(ApplicationError) as naive:
        SessionOrchestrationService.validate_discovery_range(
            datetime(2026, 8, 19),
            None,
        )
    assert naive.value.code == "DISCOVERY_DATE_TIMEZONE_REQUIRED"

    with pytest.raises(ApplicationError) as reversed_range:
        SessionOrchestrationService.validate_discovery_range(
            datetime(2026, 8, 20, tzinfo=UTC),
            datetime(2026, 8, 19, tzinfo=UTC),
        )
    assert reversed_range.value.code == "DISCOVERY_DATE_RANGE_INVALID"
