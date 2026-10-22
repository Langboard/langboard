"""Datetime snapshot operations preserve the stored timestamp and timezone."""

import pickle
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from langboard_shared.core.types import SafeDateTime


@pytest.mark.parametrize("tz", [timezone.utc, timezone(timedelta(hours=5, minutes=30))])
@pytest.mark.parametrize("fold", [0, 1])
@pytest.mark.parametrize("operation", [copy, deepcopy, *range(pickle.HIGHEST_PROTOCOL + 1)])
def test_snapshot_roundtrip_preserves_native_datetime_state(tz, fold, operation):
    source = SafeDateTime(2021, 11, 7, 1, 30, 23, 456789, tzinfo=tz, fold=fold)
    native = datetime(2021, 11, 7, 1, 30, 23, 456789, tzinfo=tz, fold=fold)
    if isinstance(operation, int):
        result = pickle.loads(pickle.dumps(source, protocol=operation))
        expected = pickle.loads(pickle.dumps(native, protocol=operation))
    else:
        result = operation(source)
        expected = operation(native)

    assert type(result) is SafeDateTime
    assert result.isoformat() == expected.isoformat()
    assert result.utcoffset() == expected.utcoffset()
    assert result.fold == expected.fold
    assert result.timestamp() == expected.timestamp()


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
@pytest.mark.parametrize("fold", [0, 1])
def test_native_naive_reconstruction_keeps_wall_time_and_normalizes_to_utc(protocol, fold):
    native = datetime(2021, 11, 7, 1, 30, 23, 456789, fold=fold)
    args = native.__reduce_ex__(protocol)[1]
    restored_native = datetime(*args)
    result = SafeDateTime(*args)

    assert result.isoformat() == restored_native.replace(tzinfo=timezone.utc).isoformat()
    assert result.tzinfo is timezone.utc
    assert result.fold == restored_native.fold


def test_naive_constructor_and_snapshots_keep_existing_utc_semantics():
    source = SafeDateTime(2021, 11, 7, 1, 30, 23, 456789, fold=1)
    assert source.tzinfo is timezone.utc
    assert source.isoformat() == "2021-11-07T01:30:23.456789+00:00"
    for result in (copy(source), deepcopy(source), pickle.loads(pickle.dumps(source))):
        assert result.isoformat() == source.isoformat()
        assert result.fold == source.fold
