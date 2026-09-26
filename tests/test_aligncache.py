"""对齐快照缓存测试：单飞并发、LRU 淘汰、不可改写、失败重试与结果一致性。

直接运行：python tests/test_aligncache.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import collation  # noqa: E402
from app.aligncache import MAX_SNAPSHOTS, SnapshotCache  # noqa: E402
from app.collation import (  # noqa: E402
    align,
    alignment_audit,
    collate,
    edition,
    provenance,
    relationships,
)


class BuildSpy:
    """统计 _build_snapshot 实际执行次数的猴子补丁。"""

    def __init__(self):
        self.calls = []
        self._orig = collation._build_snapshot

    def __enter__(self):
        def spy(base, witness):
            self.calls.append((base, witness))
            return self._orig(base, witness)

        collation._build_snapshot = spy
        return self

    def __exit__(self, *exc):
        collation._build_snapshot = self._orig


def test_single_flight_computes_once():
    # 同键并发：只有一个线程真正计算，其余等待并拿到同一对象
    cache = SnapshotCache()
    calls = []
    barrier = threading.Barrier(6)

    def build():
        calls.append(1)
        time.sleep(0.2)  # 放大并发窗口，让等待者堆上
        return {"value": 42}

    results = []

    def worker():
        barrier.wait()
        results.append(cache.get(("底本", "文本"), build))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    barrier.wait()  # 主线程为第 6 方，齐后同时放行
    for t in threads:
        t.join()
    assert calls == [1]
    assert len(results) == 5
    assert all(r is results[0] for r in results)


def test_lru_eviction_order():
    cache = SnapshotCache(capacity=3)
    builds = []

    def make(v):
        def build():
            builds.append(v)
            return v

        return build

    for k in ("a", "b", "c"):
        assert cache.get(k, make(k)) == k
    # 命中也算使用：a 变为最近使用，使用顺序变为 b、c、a
    assert cache.get("a", make("a2")) == "a"
    assert builds == ["a", "b", "c"]
    # 插入 d，淘汰最久未用的 b
    assert cache.get("d", make("d")) == "d"
    assert len(cache) == 3
    # b 已淘汰需重算；重算 b 又淘汰此刻最久未用的 c
    assert cache.get("b", make("b")) == "b"
    assert builds == ["a", "b", "c", "d", "b"]
    # 命中不重算：a、d、b 仍在
    n = len(builds)
    assert cache.get("a", make("x")) == "a"
    assert cache.get("d", make("x")) == "d"
    assert cache.get("b", make("x")) == "b"
    assert len(builds) == n
    # c 已被淘汰：重新计算
    assert cache.get("c", make("c")) == "c"
    assert builds[-1] == "c"
    assert len(cache) == 3


def test_default_capacity_is_eight():
    assert MAX_SNAPSHOTS == 8
    cache = SnapshotCache()
    builds = []

    def build(v):
        builds.append(v)
        return v

    for i in range(9):
        assert cache.get(f"k{i}", lambda i=i: build(i)) == i
    assert len(cache) == MAX_SNAPSHOTS
    # 最久未用的 k0 已淘汰，重新计算；k5 仍在，命中不重算
    assert cache.get("k0", lambda: build("k0again")) == "k0again"
    assert builds[-1] == "k0again"
    n = len(builds)
    assert cache.get("k5", lambda: build("k5again")) == 5
    assert len(builds) == n
    assert len(cache) == MAX_SNAPSHOTS


def test_inflight_snapshot_not_counted_toward_capacity():
    # 计算中的快照不占 8 个已完成名额；完成后入缓存仍只保留 8 个
    cache = SnapshotCache()
    entered = threading.Event()
    gate = threading.Event()

    def slow_build():
        entered.set()
        assert gate.wait(5)
        return "慢"

    outcome = []
    t = threading.Thread(target=lambda: outcome.append(cache.get("慢键", slow_build)))
    t.start()
    assert entered.wait(2)
    for i in range(MAX_SNAPSHOTS):
        assert cache.get(f"键{i}", lambda i=i: i) == i
    assert len(cache) == MAX_SNAPSHOTS
    gate.set()
    t.join(5)
    assert outcome == ["慢"]
    assert len(cache) == MAX_SNAPSHOTS
    assert cache.get("慢键", lambda: "重算") == "慢"


def test_failure_wakes_waiters_and_allows_retry():
    cache = SnapshotCache()
    attempts = []
    entered = threading.Event()
    gate = threading.Event()

    def failing_build():
        attempts.append(1)
        entered.set()
        assert gate.wait(5)
        raise RuntimeError("boom")

    errors = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        try:
            cache.get("k", failing_build)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    barrier.wait()
    assert entered.wait(2)
    time.sleep(0.1)  # 让另外两个等待者先阻塞在 wait 上
    gate.set()
    for t in threads:
        t.join()
    # 只算了一次；等待者被唤醒并拿到同一异常
    assert attempts == [1]
    assert sorted(errors) == ["boom"] * 3
    assert len(cache) == 0
    # 失败不留半成品：后续调用自动重试并成功
    assert cache.get("k", lambda: "ok") == "ok"
    assert cache.get("k", lambda: "不应再算") == "ok"


def test_snapshot_content_not_writable_by_callers():
    collation._SNAPSHOT_CACHE.clear()
    snapshot = collation._alignment_snapshot("天地人", "天人")
    # 代价表与首条路径为不可变结构
    assert isinstance(snapshot.cost, tuple)
    assert all(isinstance(row, tuple) for row in snapshot.cost)
    assert isinstance(snapshot.events, tuple)

    def assign_cost():
        snapshot.cost[0][0] = 99

    def assign_events():
        snapshot.events[0] = None

    for mutate in (assign_cost, assign_events):
        try:
            mutate()
        except TypeError:
            pass
        else:
            raise AssertionError("快照内容不应可改写")
    # 坐标读法为深拷贝：改写副本不影响缓存
    char_readings, gap_readings = snapshot.readings()
    char_readings[1]["reading"] = "篡改"
    char_readings[1]["span"][0] = 99
    gap_readings[2]["kind"] = "insert"
    fresh_char, fresh_gap = snapshot.readings()
    assert fresh_char[1] == {"kind": "delete", "reading": "", "span": [1, 1]}
    assert fresh_gap[2] == {"kind": "match", "reading": "", "span": [1, 1]}
    # 改写接口响应里的区间同样不污染缓存
    witnesses = [{"name": "甲", "text": "天人"}, {"name": "乙", "text": "天地人"}]
    res = collate("天地人", witnesses)
    char2 = [e for e in res["entries"] if e["coordinate"]["type"] == "char"][0]
    char2["spans"]["witnesses"]["甲"][0][0] = 99
    res2 = collate("天地人", witnesses)
    char2b = [e for e in res2["entries"] if e["coordinate"]["type"] == "char"][0]
    assert char2b["spans"]["witnesses"]["甲"] == [[1, 1]]


def test_align_keeps_list_contract():
    events = align("ab", "ba")
    assert isinstance(events, list)
    assert [e[0] for e in events] == ["substitute", "substitute"]
    # 改写返回的列表不影响缓存中的首条路径
    events[0] = ("insert", None, 0)
    assert [e[0] for e in align("ab", "ba")] == ["substitute", "substitute"]


def test_witness_name_not_in_key():
    collation._SNAPSHOT_CACHE.clear()
    with BuildSpy() as spy:
        res = collate(
            "天地人",
            [
                {"name": "甲", "text": "天池人"},
                {"name": "乙", "text": "天池人"},
                {"name": "丙", "text": "天地人"},
            ],
        )
    # 同文不同名共享同一快照：两种文本各只算一次
    assert sorted(spy.calls) == [("天地人", "天地人"), ("天地人", "天池人")]
    # 仍按原输入逐抄本分别返回
    assert [w["name"] for w in res["witnesses"]] == ["甲", "乙", "丙"]
    char2 = [
        e
        for e in res["entries"]
        if e["coordinate"] == {"type": "char", "index": 2, "label": "第2字"}
    ][0]
    groups = {g["reading"]: g["witnesses"] for g in char2["groups"]}
    assert groups["池"] == ["甲", "乙"]
    assert groups["地"] == ["丙"]


def test_query_text_shares_snapshot():
    collation._SNAPSHOT_CACHE.clear()
    witnesses = [
        {"name": "甲", "text": "天地人"},
        {"name": "乙", "text": "天地間人"},
    ]
    with BuildSpy() as spy:
        res = provenance("天地人", witnesses, "天地間人")
    # 待鉴文本与乙本同文：复用同一快照，共 2 次计算
    assert sorted(spy.calls) == [("天地人", "天地人"), ("天地人", "天地間人")]
    assert res["decidable"] is True


CASES = [
    (
        "天地人",
        [
            {"name": "甲", "text": "天地人"},
            {"name": "乙", "text": "天地間人"},
            {"name": "丙", "text": "天池人"},
            {"name": "丁", "text": "天人"},
        ],
        "天地間人",
        [{"type": "gap", "index": 2, "reading": "間"}],
    ),
    (
        "ab",
        [{"name": "甲", "text": "ba"}, {"name": "乙", "text": "ab"}],
        "ba",
        [{"type": "char", "index": 1, "reading": "b"}],
    ),
    (
        "a👍b，",
        [{"name": "甲", "text": "a👍👍b。"}, {"name": "乙", "text": "ab，"}],
        "a👍b，",
        [
            {"type": "gap", "index": 2, "reading": "👍"},
            {"type": "char", "index": 4, "reading": "。"},
        ],
    ),
]


def _all_results(base, witnesses, query, selections):
    return {
        "collate": collate(base, witnesses),
        "edition": edition(base, witnesses, selections),
        "provenance": provenance(base, witnesses, query),
        "relationships": relationships(base, witnesses),
        "audit": alignment_audit(base, witnesses),
    }


def test_results_identical_on_hit_and_after_eviction():
    for base, witnesses, query, selections in CASES:
        collation._SNAPSHOT_CACHE.clear()
        first = _all_results(base, witnesses, query, selections)
        # 全部命中缓存：结果不变
        second = _all_results(base, witnesses, query, selections)
        assert first == second
        # 用 8 个无关键挤占淘汰全部快照后重算：结果仍不变
        for i in range(MAX_SNAPSHOTS):
            collation._alignment_snapshot(f"底本{i}", f"抄本{i}")
        third = _all_results(base, witnesses, query, selections)
        assert first == third


def test_concurrent_requests_compute_once_and_agree():
    # 多路径抄本（甲 "aa" 对 "aaa" 有 3 条最优路径）并发下结果一致
    base = "aaa"
    witnesses = [{"name": "甲", "text": "aa"}, {"name": "乙", "text": "aaa"}]
    query = "aa"
    collation._SNAPSHOT_CACHE.clear()
    reference = _all_results(base, witnesses, query, [])
    assert reference["audit"]["witnesses"][0]["path_count"] == "3"

    collation._SNAPSHOT_CACHE.clear()
    barrier = threading.Barrier(9)
    results = []

    def worker(n):
        barrier.wait()
        if n % 3 == 0:
            results.append(("collate", collate(base, witnesses)))
        elif n % 3 == 1:
            results.append(("audit", alignment_audit(base, witnesses)))
        else:
            results.append(("provenance", provenance(base, witnesses, query)))

    with BuildSpy() as spy:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join()
    # 两种文本各只计算一次
    assert sorted(spy.calls) == [("aaa", "aa"), ("aaa", "aaa")]
    for kind, res in results:
        assert res == reference[kind]


def run_all():
    for name, fn in sorted(
        ((k, v) for k, v in globals().items() if k.startswith("test_")),
        key=lambda kv: kv[0],
    ):
        fn()
        print(f"PASS {name}")
    print("全部测试通过。")


if __name__ == "__main__":
    run_all()
