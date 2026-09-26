"""进程内共享快照缓存：同键单飞计算，LRU 淘汰。

- 最多保留 MAX_SNAPSHOTS 个已完成快照，按最近最少使用淘汰；
- 同键并发只计算一次：首个到达者负责计算，其余调用阻塞等待并复用结果；
- 计算在全局锁外进行，不阻塞其他键的读写；
- 计算失败时唤醒全部等待者（原样抛回异常），不留下半成品，
  后续调用自动重试。
"""

import threading
from collections import OrderedDict

MAX_SNAPSHOTS = 8


class SnapshotCache:
    """线程安全的单飞 LRU 快照缓存。"""

    def __init__(self, capacity: int = MAX_SNAPSHOTS):
        self._capacity = capacity
        self._lock = threading.Lock()
        self._done = OrderedDict()  # 已完成快照：键 -> 快照（右端为最近使用）
        self._inflight = {}  # 计算中的键 -> _Inflight（不计入容量）

    def get(self, key, build):
        """返回 key 对应的快照；缺失时由唯一线程执行 build() 计算，
        其余同键调用等待并复用同一结果。build 在锁外执行；build 抛错时
        等待者拿到同一异常，后续调用可重试。"""
        with self._lock:
            if key in self._done:
                snapshot = self._done[key]
                self._done.move_to_end(key)
                return snapshot
            inflight = self._inflight.get(key)
            owner = inflight is None
            if owner:
                inflight = _Inflight()
                self._inflight[key] = inflight

        if not owner:
            return inflight.wait()

        try:
            snapshot = build()  # 锁外计算，不占用全局锁
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(key) is inflight:
                    del self._inflight[key]
            inflight.abort(exc)  # 唤醒等待者；键已移除，后续调用可重试
            raise
        with self._lock:
            if self._inflight.get(key) is inflight:
                del self._inflight[key]
            self._done[key] = snapshot
            self._done.move_to_end(key)
            while len(self._done) > self._capacity:
                self._done.popitem(last=False)  # 淘汰最久未使用者
        inflight.deliver(snapshot)
        return snapshot

    def clear(self):
        """清空已完成快照与在途标记（不影响正在进行的计算本身）。"""
        with self._lock:
            self._done.clear()
            self._inflight.clear()

    def __len__(self):
        with self._lock:
            return len(self._done)


class _Inflight:
    """同键单飞票据：计算结束（成功或失败）时唤醒全部等待者。"""

    __slots__ = ("_event", "_snapshot", "_error")

    def __init__(self):
        self._event = threading.Event()
        self._snapshot = None
        self._error = None

    def deliver(self, snapshot):
        self._snapshot = snapshot
        self._event.set()

    def abort(self, error):
        self._error = error
        self._event.set()

    def wait(self):
        self._event.wait()
        if self._error is not None:
            raise self._error
        return self._snapshot
