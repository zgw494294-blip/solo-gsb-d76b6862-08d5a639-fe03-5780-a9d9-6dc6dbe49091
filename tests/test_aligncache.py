"""对齐快照缓存测试：单次计算与命中、LRU 淘汰、同键并发、失败唤醒与
重试、不同键并行计算、交付视图不可改写缓存正本，以及五个接口在命中、
淘汰与并发下的结果一致性。

直接运行：python tests/test_aligncache.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import collation  # noqa: E402
from app.aligncache import AlignmentSnapshotCache  # noqa: E402


def _compute_pair(key):
    """测试用快照正本：结构对齐 (cost, events, readings) 的极简版。"""
    base, text = key
    cost = tuple(
        tuple(range(len(text) + 1)) for _ in range(len(base) + 1)
    )
    events = (("match", 0, 0),)
    char_readings = [
        {"kind": "match", "reading": c, "span": [i, i + 1]}
        for i, c in enumerate(base)
    ]
    gap_readings = [
        {"kind": "match", "reading": "", "span": [0, 0]}
        for _ in range(len(base) + 1)
    ]
    return cost, events, (char_readings, gap_readings)


def test_compute_once_per_key_and_hit_returns_equal_view():
    calls = []

    def compute(key):
        calls.append(key)
        return _compute_pair(key)

    cache = AlignmentSnapshotCache(capacity=8, compute=compute)
    v1 = cache.get(("底本", "抄本"))
    v2 = cache.get(("底本", "抄本"))
    assert calls == [("底本", "抄本")]  # 同键只算一次
    assert v1.cost is v2.cost  # 代价表共享同一不可变结构
    assert v1.events is v2.events
    assert v1.readings == v2.readings
    assert v1.readings is not v2.readings  # 各次交付的读法互为独立副本
    cache.get(("底本", "另一抄本"))
    assert calls == [("底本", "抄本"), ("底本", "另一抄本")]  # 不同键各自计算


def test_view_cannot_rewrite_cache():
    cache = AlignmentSnapshotCache(capacity=8, compute=_compute_pair)
    view = cache.get(("底", "抄"))
    # 改写交付视图中的读法：不影响缓存正本
    view.readings[0][0]["reading"] = "改写"
    view.readings[0][0]["span"][0] = 99
    again = cache.get(("底", "抄"))
    assert again.readings[0][0]["reading"] == "底"
    assert again.readings[0][0]["span"] == [0, 1]
    # 共享的代价表与路径事件本身不可变
    for target in (view.cost, view.events):
        try:
            target[0] = None
        except TypeError:
            pass
        else:
            raise AssertionError("共享结构应不可改写")


def test_lru_eviction():
    calls = []

    def compute(key):
        calls.append(key)
        return _compute_pair(key)

    cache = AlignmentSnapshotCache(capacity=3, compute=compute)
    keys = [("b", f"w{i}") for i in range(3)]
    for k in keys:
        cache.get(k)
    cache.get(keys[0])  # 命中并刷新为最近使用：顺序变为 k1 k2 k0
    cache.get(("b", "w3"))  # 超出容量：淘汰最久未用的 k1
    assert calls == keys + [("b", "w3")]
    cache.get(keys[0])  # k0 仍在
    cache.get(keys[2])  # k2 仍在
    assert calls == keys + [("b", "w3")]
    cache.get(keys[1])  # k1 已被淘汰：重新计算
    assert calls == keys + [("b", "w3"), keys[1]]


def test_same_key_concurrent_compute_once():
    calls = []

    def compute(key):
        calls.append(key)
        time.sleep(0.05)  # 拖长计算窗口，让并发请求重叠
        return _compute_pair(key)

    cache = AlignmentSnapshotCache(capacity=8, compute=compute)
    results = []
    barrier = threading.Barrier(9)

    def worker():
        barrier.wait()
        results.append(cache.get(("底", "抄")))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    barrier.wait()  # 同时放行
    for t in threads:
        t.join()
    assert calls == [("底", "抄")]  # 同键并发只计算一次
    first = results[0]
    assert len(results) == 8
    for r in results[1:]:
        assert r.cost is first.cost  # 等待者复用同一份结果
        assert r.readings == first.readings
        assert r.readings is not first.readings


def test_failure_wakes_waiters_and_allows_retry():
    calls = []
    proceed = threading.Event()
    fail = threading.Event()
    fail.set()

    def compute(key):
        calls.append(key)
        proceed.wait(5)  # 等全部并发调用进入在途等待
        if fail.is_set():
            raise RuntimeError("计算失败")
        return _compute_pair(key)

    cache = AlignmentSnapshotCache(capacity=8, compute=compute)
    errors = []
    barrier = threading.Barrier(5)

    def worker():
        barrier.wait()
        try:
            cache.get(("底", "抄"))
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    barrier.wait()
    time.sleep(0.2)  # 让等待者都进入在途等待
    proceed.set()
    for t in threads:
        t.join()
    assert errors == ["计算失败"] * 4  # 等待者被唤醒并收到同一失败
    assert calls == [("底", "抄")]  # 失败的那次只算了一遍
    fail.clear()
    snap = cache.get(("底", "抄"))  # 失败不留残态：后续调用重试成功
    assert snap.readings[0][0]["reading"] == "底"
    assert calls == [("底", "抄"), ("底", "抄")]


def test_different_keys_compute_in_parallel():
    # 计算不占全局锁：a 的计算等到 b 的计算开始后才返回；
    # 若计算期间持锁，b 无法开始，a 将超时失败
    a_started = threading.Event()
    b_started = threading.Event()

    def compute(key):
        if key[1] == "a":
            a_started.set()
            assert b_started.wait(5), "b 的计算未能开始：计算疑似持有全局锁"
        else:
            b_started.set()
        return _compute_pair(key)

    cache = AlignmentSnapshotCache(capacity=8, compute=compute)
    outcome = []

    def get_a():
        outcome.append(cache.get(("底", "a")))

    t = threading.Thread(target=get_a)
    t.start()
    assert a_started.wait(5)
    cache.get(("底", "b"))  # 主线程算 b：应能与 a 并行
    t.join()
    assert len(outcome) == 1


BASE = "天地人"
W4 = [
    {"name": "甲", "text": "天地人"},
    {"name": "乙", "text": "天地間人"},
    {"name": "丙", "text": "天池人"},
    {"name": "丁", "text": "天人"},
]


def _all_results(base, witnesses, query):
    """五个接口一次全量结果（与 main.py 各端点背后的计算一致）。"""
    return {
        "collate": collation.collate(base, witnesses),
        "edition": collation.edition(
            base,
            witnesses,
            [
                {"type": "gap", "index": 2, "reading": "間"},
                {"type": "char", "index": 2, "reading": "池"},
            ],
        ),
        "provenance": collation.provenance(base, witnesses, query),
        "relationships": collation.relationships(base, witnesses),
        "audit": collation.alignment_audit(base, witnesses),
    }


def test_results_stable_across_hits_and_eviction():
    expected = _all_results(BASE, W4, "天地間人")
    again = _all_results(BASE, W4, "天地間人")  # 全部命中缓存
    assert again == expected
    # 灌入 12 组不同的 (底本, 文本)，把上面的快照全部淘汰（容量 8）
    for i in range(12):
        collation.collate(
            f"擾{i}亂",
            [
                {"name": "甲", "text": f"擾{i}亂"},
                {"name": "乙", "text": f"擾亂{i}"},
            ],
        )
    recomputed = _all_results(BASE, W4, "天地間人")  # 淘汰后重算
    assert recomputed == expected


def test_same_text_different_names_returned_separately():
    # 同文不同名：共享同一快照（抄本名不进键），响应仍按各自录入分别列出
    witnesses = [
        {"name": "甲", "text": "天地間人"},
        {"name": "乙", "text": "天地間人"},
    ]
    res = collation.collate(BASE, witnesses)
    gap2 = [
        e
        for e in res["entries"]
        if e["coordinate"] == {"type": "gap", "index": 2, "label": "第2字后间隙"}
    ][0]
    group = {g["reading"]: g for g in gap2["groups"]}["間"]
    assert group["witnesses"] == ["甲", "乙"]  # 两名各自列入
    assert gap2["spans"]["witnesses"]["甲"] == [[2, 3]]
    assert gap2["spans"]["witnesses"]["乙"] == [[2, 3]]
    audit = collation.alignment_audit(BASE, witnesses)
    assert [w["name"] for w in audit["witnesses"]] == ["甲", "乙"]
    assert [w["path_count"] for w in audit["witnesses"]] == ["1", "1"]
    # 键为原样 (底本, 文本)：两名同文只对应一份快照
    assert ("天地人", "天地間人") in collation._SNAPSHOTS._done


def test_concurrent_endpoints_consistent():
    # 多线程并发调用五个接口：结果与串行一致
    expected = _all_results(BASE, W4, "天地間人")
    errors = []

    def worker():
        try:
            for _ in range(3):
                if _all_results(BASE, W4, "天地間人") != expected:
                    errors.append("并发结果与串行不一致")
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


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
