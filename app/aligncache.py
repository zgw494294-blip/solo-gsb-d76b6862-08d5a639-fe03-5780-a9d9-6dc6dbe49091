"""进程内共享对齐快照缓存。

以原样 (base, text) 为键，缓存一次全局对齐的共享结果：最小代价表、
字典序首条路径（操作事件序列）及其坐标读法。抄本名不进入键；同文
不同名的抄本共享同一快照，各接口响应仍按各自录入分别生成。

- 最多保留 capacity 份已完成快照，按最近最少使用（LRU）淘汰；
- 同键并发只计算一次，其余调用等待并复用结果（single-flight）；
- 计算失败时唤醒全部等待者并清除在途标记，后续调用可重试；
- 计算在全局锁外进行，不同键的计算互不阻塞；
- 交付调用方的快照视图中，代价表与路径事件为共享的不可变结构，
  坐标读法为本次调用私有的深拷贝——缓存正本不会被调用方改写。
"""

import copy
import threading
from collections import OrderedDict


class Snapshot:
    """单次调用交付的对齐快照视图。

    cost：最小代价表，tuple[tuple[int, ...], ...]，共享不可变结构；
    events：字典序首条路径的操作事件，tuple[(op, i, j), ...]，共享不可变结构；
    readings：坐标读法 (char_readings, gap_readings)，本次调用私有的深拷贝。
    """

    __slots__ = ("cost", "events", "readings")

    def __init__(self, cost, events, readings):
        self.cost = cost
        self.events = events
        self.readings = readings


class _Inflight:
    """同键在途计算：负责计算的调用完成后 set，等待者醒后取结果或异常。"""

    __slots__ = ("done", "snapshot", "error")

    def __init__(self):
        self.done = threading.Event()
        self.snapshot = None
        self.error = None


class AlignmentSnapshotCache:
    """(base, text) → 对齐快照 的进程内缓存（LRU + single-flight）。

    compute(key) 负责算出快照正本 (cost, events, readings)：cost／events
    须为不可变结构（元组），readings 正本由本缓存持有、绝不外泄，
    每次交付前深拷贝。
    """

    def __init__(self, capacity: int, compute):
        self._capacity = capacity
        self._compute = compute
        self._lock = threading.Lock()
        self._done = OrderedDict()  # key -> 已完成快照正本，按最近使用排序
        self._inflight = {}  # key -> _Inflight；在途计算不计入容量、不参与淘汰

    def get(self, key) -> Snapshot:
        """取 key 的对齐快照视图；未命中或已淘汰则现算（同键并发只算一次）。"""
        cost, events, readings = self._canonical(key)
        return Snapshot(cost, events, copy.deepcopy(readings))

    def _canonical(self, key):
        with self._lock:
            hit = self._done.get(key)
            if hit is not None:
                self._done.move_to_end(key)
                return hit
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = _Inflight()
                self._inflight[key] = inflight
                owner = True
            else:
                owner = False
        if not owner:
            # 同键并发：等待唯一的那次计算，复用其结果或异常
            inflight.done.wait()
            if inflight.error is not None:
                raise inflight.error
            return inflight.snapshot
        try:
            snapshot = self._compute(key)  # 全局锁外计算
        except BaseException as exc:
            # 唤醒等待者并清除在途标记，后续调用可重试
            with self._lock:
                if self._inflight.get(key) is inflight:
                    del self._inflight[key]
            inflight.error = exc
            inflight.done.set()
            raise
        with self._lock:
            if self._inflight.get(key) is inflight:
                del self._inflight[key]
            self._done[key] = snapshot
            while len(self._done) > self._capacity:
                self._done.popitem(last=False)
        inflight.snapshot = snapshot
        inflight.done.set()
        return snapshot
