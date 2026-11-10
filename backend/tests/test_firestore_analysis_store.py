"""Firestoreの文書操作を模擬し、共有・予約・読書き回数を確認する。"""

from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

import pytest

from app.services.firestore_analysis_store import (
    MAX_COMPLETED_IDS,
    AnalysisStateError,
    FirestoreAnalysisWindowStore,
)


@pytest.fixture
def database(monkeypatch):
    documents = {}
    counts = {"reads": 0, "writes": 0}
    lock = Lock()

    class Reference:
        def __init__(self, key):
            self.key = key

        def get(self, **kwargs):
            counts["reads"] += 1
            value = deepcopy(documents.get(self.key))
            return SimpleNamespace(to_dict=lambda: value)

    class Transaction:
        def __init__(self):
            self.writes = []

        def set(self, ref, data):
            self.writes.append((ref.key, deepcopy(data)))

    client = SimpleNamespace(
        collection=lambda name: SimpleNamespace(document=lambda key: Reference((name, key))),
        transaction=lambda **kwargs: Transaction(),
    )

    def transactional(function):
        def run(transaction):
            with lock:
                result = function(transaction)
                for key, value in transaction.writes:
                    documents[key] = value
                    counts["writes"] += 1
                return result
        return run

    monkeypatch.setattr("app.services.firestore_analysis_store._client", lambda *args: client)
    monkeypatch.setattr(
        "app.services.firestore_analysis_store.firestore.transactional", transactional,
    )
    return documents, counts


def test_shared_state_waits_for_all_new_ids_and_survives_client_recreation(database):
    documents, counts = database
    first = FirestoreAnalysisWindowStore("test-project")
    owner = first.claim("s", ["u1", "u2", "u3", "u4", "u5"], 60)
    assert owner
    assert counts == {"reads": 1, "writes": 1}
    first.finish(owner, True)
    assert counts == {"reads": 2, "writes": 2}
    second = FirestoreAnalysisWindowStore("test-project")
    assert second.claim("s", ["u2", "u3", "u4", "u5", "u6"], 60) is None
    assert counts == {"reads": 3, "writes": 2}
    assert second.claim("s", ["u6", "u7", "u8", "u9", "u10"], 60)
    assert len(documents) == 1
    assert len(next(iter(documents))[1]) == 64
    assert "u1" not in str(documents)


def test_pending_reservation_blocks_another_request_and_failure_releases(database):
    store = FirestoreAnalysisWindowStore("test-project")
    owner = store.claim("s", ["u1"], 60)
    assert store.claim("s", ["u1"], 60) is None
    assert store.claim("s", ["u2"], 60) is None
    store.finish(owner, False)
    assert store.claim("s", ["u1"], 60)


def test_first_count_condition_is_only_for_initial_analysis(database):
    store = FirestoreAnalysisWindowStore("test-project")
    assert store.claim("s", ["u1"], 60, initial_ready=False) is None
    owner = store.claim("s", ["u1"], 60, initial_ready=True)
    store.finish(owner, True)
    assert store.claim("s", ["u2"], 60, initial_ready=False)


def test_expired_owner_cannot_overwrite_new_reservation(database, monkeypatch):
    store = FirestoreAnalysisWindowStore("test-project")
    monkeypatch.setattr("app.services.firestore_analysis_store.time.time", lambda: 100)
    old = store.claim("s", ["u1"], 10)
    monkeypatch.setattr("app.services.firestore_analysis_store.time.time", lambda: 111)
    new = store.claim("s", ["u1"], 10)
    assert new
    with pytest.raises(AnalysisStateError):
        store.finish(old, True)
    store.finish(new, True)
    assert store.claim("s", ["u1"], 60) is None


def test_session_ids_isolate_the_same_utterance_id(database):
    store = FirestoreAnalysisWindowStore("test-project")
    owner = store.claim("s1", ["u1"], 60)
    store.finish(owner, True)
    assert store.claim("s2", ["u1"], 60)


def test_capacity_fails_without_forgetting_old_ids(database):
    documents, counts = database
    store = FirestoreAnalysisWindowStore("test-project")
    owner = store.claim("s", [f"u{i}" for i in range(MAX_COMPLETED_IDS)], 60)
    store.finish(owner, True)
    before = counts["writes"]
    with pytest.raises(AnalysisStateError):
        store.claim("s", ["extra"], 60)
    assert counts["writes"] == before
    assert len(next(iter(documents.values()))["completed_ids"]) == MAX_COMPLETED_IDS


def test_empty_or_duplicate_ids_do_not_read_firestore(database):
    _, counts = database
    store = FirestoreAnalysisWindowStore("test-project")
    assert store.claim("s", [], 60) is None
    assert store.claim("s", ["u1", "u1"], 60) is None
    assert counts == {"reads": 0, "writes": 0}


def test_simultaneous_reservations_have_one_owner(database):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(
            lambda _: FirestoreAnalysisWindowStore("test-project").claim("s", ["u1"], 60),
            range(2),
        ))
    assert sum(owner is not None for owner in owners) == 1


def test_emulator_and_production_clients_do_not_share_cache(monkeypatch):
    from unittest.mock import Mock

    from google.auth.credentials import AnonymousCredentials

    from app.services.firestore_analysis_store import _client

    constructor = Mock()
    monkeypatch.setattr("app.services.firestore_analysis_store.firestore.Client", constructor)
    _client.cache_clear()
    try:
        _client("demo-cache-test", "(default)", "127.0.0.1:8787")
        _client("demo-cache-test", "(default)", "")
        assert constructor.call_count == 2
        assert isinstance(constructor.call_args_list[0].kwargs["credentials"], AnonymousCredentials)
        assert constructor.call_args_list[1].kwargs["credentials"] is None
    finally:
        _client.cache_clear()


def test_aborted_transaction_releases_and_retries_with_fresh_transaction(database, monkeypatch):
    from unittest.mock import Mock

    from google.api_core.exceptions import Aborted

    store = FirestoreAnalysisWindowStore("test-project")
    wrapped = ValueError("Failed to commit transaction")
    wrapped.__cause__ = Aborted("Transaction lock timeout")
    operation = Mock(side_effect=[wrapped, "reserved"])
    sleep = Mock()
    monkeypatch.setattr("app.services.firestore_analysis_store.time.sleep", sleep)
    assert store._run_transaction(operation) == "reserved"
    assert operation.call_count == 2
    assert operation.call_args_list[0].args[0] is not operation.call_args_list[1].args[0]
    sleep.assert_called_once()


def test_transaction_retry_is_bounded(database, monkeypatch):
    from unittest.mock import Mock

    from google.api_core.exceptions import Aborted

    store = FirestoreAnalysisWindowStore("test-project")
    error = ValueError("Failed to commit transaction")
    error.__cause__ = Aborted("Transaction lock timeout")
    operation = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr("app.services.firestore_analysis_store.time.sleep", sleep)
    with pytest.raises(ValueError):
        store._run_transaction(operation)
    assert operation.call_count == 3
    assert sleep.call_count == 2
