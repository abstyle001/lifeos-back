"""私聊 SSE 推送的进程内 pub/sub hub。

设计要点：
- FastAPI 的同步 REST 端点跑在线程池里；SSE 生成器跑在 asyncio 事件循环里。
  `publish()` 被同步端点调用，必须线程安全地把事件塞进对应订阅者的 asyncio.Queue。
- 桥接方式：订阅时同时保存 (queue, loop)；发布时用 `loop.call_soon_threadsafe`
  调度 `_safe_put` 到目标 loop 上执行，避免跨线程直接调用 `queue.put_nowait`。
- 单个用户可能开多个标签页/设备，每个 SSE 连接一个 Queue；hub 用 list 保存。
- Queue 有界（256）+ 满则丢最旧：防止慢客户端把内存吃爆，也保证新消息不会被卡住。
- MVP 只在进程内广播；Vercel Serverless 跨实例推送由前端兜底轮询弥补，
  量起来后升 Redis Pub/Sub（见 docs/direct-chat-design.md §6.2 §12）。
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any

# 单个 SSE 订阅者的事件缓冲上限；满则丢最旧
QUEUE_MAX_SIZE = 256


def _safe_put(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    """在事件循环线程内执行；queue 满时丢最旧一条再重试。"""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 极端情况：仍然满，直接丢弃本次事件（不阻塞发布方）
            pass


class ChatHub:
    """进程内单例：user_id → [(queue, loop), ...]。"""

    def __init__(self) -> None:
        self._subs: dict[int, list[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]]] = (
            defaultdict(list)
        )
        self._lock = threading.Lock()

    def register(
        self,
        user_id: int,
        queue: asyncio.Queue[dict[str, Any]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """SSE 建连时调用（在 async 上下文里）。"""
        with self._lock:
            self._subs[user_id].append((queue, loop))

    def unregister(self, user_id: int, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """SSE 断开时调用；按 queue 身份移除（同一用户可能有多个订阅）。"""
        with self._lock:
            subs = self._subs.get(user_id)
            if not subs:
                return
            self._subs[user_id] = [(q, loop) for q, loop in subs if q is not queue]
            if not self._subs[user_id]:
                # 清理空列表，避免 dict 无限增长
                del self._subs[user_id]

    def publish(self, user_id: int, event: dict[str, Any]) -> None:
        """给指定用户的所有 SSE 订阅推事件；可从同步线程安全调用。

        - 无订阅者时静默 no-op（用户可能不在线，消息已入库，下次拉列表即可见）。
        - 订阅者的 loop 已关闭时（客户端断开未及时清理）忽略该订阅。
        """
        with self._lock:
            subs = list(self._subs.get(user_id, ()))
        for queue, loop in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, event)
            except RuntimeError:
                # loop 已关闭；订阅会在 SSE 生成器的 finally 里被清理
                continue

    def subscriber_count(self, user_id: int) -> int:
        """测试与监控辅助：返回某用户当前的订阅连接数。"""
        with self._lock:
            return len(self._subs.get(user_id, ()))


# 进程内单例，被 routers/chat.py 的 SSE 生成器与 services/chat.py 的发消息路径共用
hub = ChatHub()
