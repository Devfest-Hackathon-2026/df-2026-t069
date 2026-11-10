"""分析済みID・同時実行・失敗後の再試行を実DBで確認する。"""

from app.services.analysis_window_store import AnalysisWindowStore


def test_overlapping_window_waits_until_all_ids_are_new(tmp_path):
    store = AnalysisWindowStore(tmp_path / "state.sqlite3")
    first = store.claim("s", ["u1", "u2", "u3", "u4", "u5"], 60)
    assert first is not None
    store.finish(first, success=True)
    assert store.claim("s", ["u2", "u3", "u4", "u5", "u6"], 60) is None
    assert store.claim("s", ["u5", "u6", "u7", "u8", "u9"], 60) is None
    assert store.claim("s", ["u6", "u7", "u8", "u9", "u10"], 60) is not None


def test_pending_ids_block_other_store_and_failure_releases_them(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = AnalysisWindowStore(path)
    other_process_store = AnalysisWindowStore(path)
    owner = store.claim("s", ["u1"], 60)
    assert owner is not None
    assert other_process_store.claim("s", ["u1"], 60) is None
    store.finish(owner, success=False)
    assert other_process_store.claim("s", ["u1"], 60) is not None


def test_completed_ids_survive_store_recreation_and_are_session_scoped(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = AnalysisWindowStore(path)
    owner = store.claim("s1", ["u1"], 60)
    store.finish(owner, success=True)
    assert AnalysisWindowStore(path).claim("s1", ["u1"], 60) is None
    assert AnalysisWindowStore(path).claim("s2", ["u1"], 60) is not None


def test_expired_claim_can_retry_without_old_owner_overwriting_it(tmp_path, monkeypatch):
    store = AnalysisWindowStore(tmp_path / "state.sqlite3")
    monkeypatch.setattr("app.services.analysis_window_store.time.time", lambda: 100)
    old = store.claim("s", ["u1"], 10)
    monkeypatch.setattr("app.services.analysis_window_store.time.time", lambda: 111)
    current = store.claim("s", ["u1"], 10)
    assert current is not None
    store.finish(old, success=False)
    assert store.claim("s", ["u1"], 10) is None


def test_empty_and_duplicate_ids_are_not_claimed(tmp_path):
    store = AnalysisWindowStore(tmp_path / "state.sqlite3")
    assert store.claim("s", [], 60) is None
    assert store.claim("s", ["u1", "u1"], 60) is None


def test_simultaneous_claims_have_only_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "state.sqlite3"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: AnalysisWindowStore(path).claim("s", ["u1", "u2"], 60), range(2),
        ))
    assert sum(owner is not None for owner in results) == 1


def test_initial_count_condition_is_lifted_after_first_success(tmp_path):
    store = AnalysisWindowStore(tmp_path / "state.sqlite3")
    assert store.claim("s", ["u1"], 60, initial_ready=False) is None
    owner = store.claim("s", ["u1"], 60, initial_ready=True)
    store.finish(owner, success=True)
    assert store.claim("s", ["u2"], 60, initial_ready=False) is not None
