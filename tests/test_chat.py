"""用户私聊（Direct Chat）端点测试。

覆盖：
- 鉴权、门槛（关注/私密/自己）、错误码统一
- 消息 CRUD + client_message_id 幂等 + 频控 + 长度校验
- 会话列表排序、未读计数、markRead、软删除与自动取消隐藏
- 游标分页
- 会话建立后对方转私密的行为
- ChatHub 单元测试（asyncio 桥接、线程安全 publish）
- SSE /stream 鉴权
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from back.services.chat import reset_rate_limits
from back.services.chat_hub import ChatHub


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """每个测试前清空模块级频控字典，避免用户 id 复用导致的污染。"""
    reset_rate_limits()
    yield
    reset_rate_limits()


def _auth_headers(client, username: str) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": "secret123"}
    )
    assert registered.status_code == 201
    token = registered.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _set_public(client, headers: dict[str, str], value: bool = True) -> None:
    resp = client.patch(
        "/api/profiles/me/settings", headers=headers, json={"is_public": value}
    )
    assert resp.status_code == 200


def _follow(client, follower_headers: dict[str, str], target_username: str) -> None:
    resp = client.post(f"/api/follows/{target_username}", headers=follower_headers)
    assert resp.status_code == 200, resp.text


def _start_conv(client, headers: dict[str, str], peer: str) -> dict:
    resp = client.post("/api/chat/conversations", headers=headers, json={"peer": peer})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _send(client, headers: dict[str, str], conv_id: int, content: str, cmid: str | None = None):
    body: dict = {"content": content}
    if cmid is not None:
        body["client_message_id"] = cmid
    return client.post(f"/api/chat/conversations/{conv_id}/messages", headers=headers, json=body)


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def test_chat_endpoints_require_auth(client):
    assert client.post("/api/chat/conversations", json={"peer": "x"}).status_code == 401
    assert client.get("/api/chat/conversations").status_code == 401
    assert client.get("/api/chat/unread-count").status_code == 401
    assert client.get("/api/chat/conversations/1/messages").status_code == 401
    assert client.post("/api/chat/conversations/1/messages", json={"content": "hi"}).status_code == 401
    assert client.post("/api/chat/conversations/1/read", json={}).status_code == 401
    assert client.delete("/api/chat/conversations/1").status_code == 401
    assert client.get("/api/chat/stream").status_code == 401


# ---------------------------------------------------------------------------
# 发起会话
# ---------------------------------------------------------------------------


def test_cannot_start_conversation_with_self(client):
    alice = _auth_headers(client, "cselfalice")
    _set_public(client, alice)
    resp = client.post("/api/chat/conversations", headers=alice, json={"peer": "cselfalice"})
    assert resp.status_code == 400
    assert "自己" in resp.json()["detail"]


def test_cannot_start_conversation_with_private_or_missing_user(client):
    viewer = _auth_headers(client, "cpviewer")
    private_user = _auth_headers(client, "cphidden")  # 未公开

    # 私密用户：即使关注了也不能发起（关注本身也会 404）
    assert client.post("/api/chat/conversations", headers=viewer, json={"peer": "cphidden"}).status_code == 404
    # 不存在用户
    assert client.post("/api/chat/conversations", headers=viewer, json={"peer": "ghost"}).status_code == 404


def test_cannot_start_conversation_without_following(client):
    alice = _auth_headers(client, "cnfalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "cnfbob")

    resp = client.post("/api/chat/conversations", headers=bob, json={"peer": "cnfalice"})
    assert resp.status_code == 400
    assert "关注" in resp.json()["detail"]


def test_start_conversation_after_follow_is_idempotent(client):
    alice = _auth_headers(client, "idcma")
    _set_public(client, alice)
    bob = _auth_headers(client, "idcmb")
    _follow(client, bob, "idcma")

    first = _start_conv(client, bob, "idcma")
    second = _start_conv(client, bob, "idcma")
    assert first["id"] == second["id"]
    assert first["peer"]["username"] == "idcma"
    assert first["last_message"] is None
    assert first["unread_count"] == 0


def test_conversation_pair_is_unique_regardless_of_initiator(client):
    """(A→B) 与 (B→A) 应命中同一会话记录（user_a_id < user_b_id 规约）。"""
    alice = _auth_headers(client, "pqalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "pqbob")
    _set_public(client, bob)
    _follow(client, bob, "pqalice")
    _follow(client, alice, "pqbob")

    from_bob = _start_conv(client, bob, "pqalice")
    from_alice = _start_conv(client, alice, "pqbob")
    assert from_bob["id"] == from_alice["id"]


# ---------------------------------------------------------------------------
# 发送与读取消息
# ---------------------------------------------------------------------------


def test_send_and_fetch_messages(client):
    alice = _auth_headers(client, "smalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "smbob")
    _set_public(client, bob)
    _follow(client, bob, "smalice")
    _follow(client, alice, "smbob")
    conv = _start_conv(client, bob, "smalice")

    r1 = _send(client, bob, conv["id"], "你好 alice")
    assert r1.status_code == 201, r1.text
    m1 = r1.json()
    assert m1["content"] == "你好 alice"
    assert m1["sender_username"] == "smbob"

    # 双方都能看到消息
    for headers in (alice, bob):
        msgs = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=headers)
        assert msgs.status_code == 200
        assert len(msgs.json()) == 1
        assert msgs.json()[0]["id"] == m1["id"]


def test_non_member_cannot_read_messages(client):
    alice = _auth_headers(client, "nmralice")
    _set_public(client, alice)
    bob = _auth_headers(client, "nmrbob")
    _set_public(client, bob)
    mallory = _auth_headers(client, "nmrmallory")
    _follow(client, bob, "nmralice")
    conv = _start_conv(client, bob, "nmralice")

    resp = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=mallory)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "用户不存在或不可见"


def test_message_length_limit(client):
    alice = _auth_headers(client, "lenalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "lenbob")
    _follow(client, bob, "lenalice")
    conv = _start_conv(client, bob, "lenalice")

    resp = _send(client, bob, conv["id"], "x" * 2001)
    assert resp.status_code == 422


def test_whitespace_only_message_rejected(client):
    alice = _auth_headers(client, "wsalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "wsbob")
    _follow(client, bob, "wsalice")
    conv = _start_conv(client, bob, "wsalice")

    resp = _send(client, bob, conv["id"], "   \n\t  ")
    # 长度校验通过（>=1），但服务端 strip 后为空 → 400
    assert resp.status_code == 400
    assert "空" in resp.json()["detail"]


def test_client_message_id_is_idempotent(client):
    alice = _auth_headers(client, "cmialice")
    _set_public(client, alice)
    bob = _auth_headers(client, "cmibob")
    _follow(client, bob, "cmialice")
    conv = _start_conv(client, bob, "cmialice")

    r1 = _send(client, bob, conv["id"], "重试测试", cmid="uuid-1234")
    r2 = _send(client, bob, conv["id"], "重试测试", cmid="uuid-1234")
    assert r1.status_code == 201
    # 幂等命中：返回既有消息，状态码仍是 201（FastAPI 已声明）
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    msgs = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=bob).json()
    assert len(msgs) == 1


def test_rate_limit_blocks_third_message_within_one_second(client):
    alice = _auth_headers(client, "rlalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "rlbob")
    _follow(client, bob, "rlalice")
    conv = _start_conv(client, bob, "rlalice")

    assert _send(client, bob, conv["id"], "1").status_code == 201
    assert _send(client, bob, conv["id"], "2").status_code == 201
    r3 = _send(client, bob, conv["id"], "3")
    assert r3.status_code == 429
    assert "频繁" in r3.json()["detail"]

    # 等窗口过去后应恢复
    time.sleep(1.1)
    assert _send(client, bob, conv["id"], "4").status_code == 201


# ---------------------------------------------------------------------------
# 关注状态变化后的行为
# ---------------------------------------------------------------------------


def test_mutual_unfollow_blocks_new_messages(client):
    alice = _auth_headers(client, "mualice")
    _set_public(client, alice)
    bob = _auth_headers(client, "mubob")
    _set_public(client, bob)
    _follow(client, bob, "mualice")
    _follow(client, alice, "mubob")
    conv = _start_conv(client, bob, "mualice")
    assert _send(client, bob, conv["id"], "hi").status_code == 201

    # 双方互取关
    assert client.delete("/api/follows/mualice", headers=bob).status_code == 200
    assert client.delete("/api/follows/mubob", headers=alice).status_code == 200

    time.sleep(1.1)  # 避开频控窗口
    resp = _send(client, bob, conv["id"], "还在吗")
    assert resp.status_code == 403
    assert "关注" in resp.json()["detail"]

    # 历史消息仍可读
    msgs = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=bob)
    assert msgs.status_code == 200
    assert len(msgs.json()) == 1


def test_one_sided_unfollow_still_allows_messages(client):
    alice = _auth_headers(client, "osualice")
    _set_public(client, alice)
    bob = _auth_headers(client, "osubob")
    _set_public(client, bob)
    _follow(client, bob, "osualice")
    _follow(client, alice, "osubob")
    conv = _start_conv(client, bob, "osualice")

    # bob 单方取关 alice，但 alice 还关注 bob → 至少一方仍在关注，允许继续
    assert client.delete("/api/follows/osualice", headers=bob).status_code == 200
    time.sleep(1.1)
    assert _send(client, bob, conv["id"], "还能发").status_code == 201
    assert _send(client, alice, conv["id"], "能收到").status_code == 201


def test_peer_becoming_private_blocks_new_messages(client):
    alice = _auth_headers(client, "pbpalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "pbpbob")
    _set_public(client, bob)
    _follow(client, bob, "pbpalice")
    _follow(client, alice, "pbpbob")
    conv = _start_conv(client, bob, "pbpalice")
    assert _send(client, bob, conv["id"], "hi").status_code == 201

    # alice 转私密
    _set_public(client, alice, False)

    time.sleep(1.1)
    resp = _send(client, bob, conv["id"], "你还在吗")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "用户不存在或不可见"

    # 历史消息双方仍可读
    assert client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=bob).status_code == 200
    assert client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=alice).status_code == 200


# ---------------------------------------------------------------------------
# 会话列表、未读计数、markRead、软删除
# ---------------------------------------------------------------------------


def test_conversation_list_ordered_by_last_message_at_desc(client):
    alice = _auth_headers(client, "clalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "clbob")
    _set_public(client, bob)
    carol = _auth_headers(client, "clcarol")
    _set_public(client, carol)
    _follow(client, alice, "clbob")
    _follow(client, alice, "clcarol")
    _follow(client, bob, "clalice")
    _follow(client, carol, "clalice")

    conv_bob = _start_conv(client, alice, "clbob")
    conv_carol = _start_conv(client, alice, "clcarol")

    # 先跟 bob 发，再跟 carol 发；alice 的会话列表应是 carol 在前
    assert _send(client, alice, conv_bob["id"], "hey bob").status_code == 201
    time.sleep(1.1)  # 频控 + 保证 created_at 严格递增
    assert _send(client, alice, conv_carol["id"], "hey carol").status_code == 201

    lst = client.get("/api/chat/conversations", headers=alice).json()
    assert len(lst) == 2
    assert lst[0]["peer"]["username"] == "clcarol"
    assert lst[1]["peer"]["username"] == "clbob"
    assert lst[0]["last_message"]["content"] == "hey carol"


def test_unread_count_and_mark_read(client):
    alice = _auth_headers(client, "uralice")
    _set_public(client, alice)
    bob = _auth_headers(client, "urbob")
    _set_public(client, bob)
    _follow(client, bob, "uralice")
    _follow(client, alice, "urbob")
    conv = _start_conv(client, bob, "uralice")

    # bob 给 alice 发 3 条
    for i, text in enumerate(["a", "b", "c"]):
        assert _send(client, bob, conv["id"], text).status_code == 201
        if i < 2:
            time.sleep(1.1)  # 避开 2/秒 频控

    # alice 未读 = 3
    unread = client.get("/api/chat/unread-count", headers=alice).json()
    assert unread["total"] == 3

    # bob 自己未读 = 0（都是他发的）
    assert client.get("/api/chat/unread-count", headers=bob).json()["total"] == 0

    # 会话列表也带未读
    lst = client.get("/api/chat/conversations", headers=alice).json()
    assert lst[0]["unread_count"] == 3

    # alice 标记已读
    r = client.post(f"/api/chat/conversations/{conv['id']}/read", headers=alice, json={})
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert client.get("/api/chat/unread-count", headers=alice).json()["total"] == 0

    # markRead 单调：不能把 last_read 往回退
    msgs = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=alice).json()
    first_id = msgs[0]["id"]
    r = client.post(
        f"/api/chat/conversations/{conv['id']}/read", headers=alice, json={"message_id": first_id}
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0  # 依然为 0，未回退


def test_hide_conversation_only_affects_self(client):
    alice = _auth_headers(client, "hdalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "hdbob")
    _set_public(client, bob)
    _follow(client, bob, "hdalice")
    _follow(client, alice, "hdbob")
    conv = _start_conv(client, bob, "hdalice")
    assert _send(client, bob, conv["id"], "hi").status_code == 201

    # alice 隐藏
    assert client.delete(f"/api/chat/conversations/{conv['id']}", headers=alice).status_code == 204
    alice_lst = client.get("/api/chat/conversations", headers=alice).json()
    assert alice_lst == []
    # bob 侧不受影响
    bob_lst = client.get("/api/chat/conversations", headers=bob).json()
    assert len(bob_lst) == 1


def test_hidden_conversation_auto_unhides_on_new_message(client):
    alice = _auth_headers(client, "ahalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "ahbob")
    _set_public(client, bob)
    _follow(client, bob, "ahalice")
    _follow(client, alice, "ahbob")
    conv = _start_conv(client, bob, "ahalice")

    # alice 隐藏空会话
    assert client.delete(f"/api/chat/conversations/{conv['id']}", headers=alice).status_code == 204
    assert client.get("/api/chat/conversations", headers=alice).json() == []

    # bob 发消息 → alice 端应自动取消隐藏且未读 = 1
    assert _send(client, bob, conv["id"], "在吗").status_code == 201
    lst = client.get("/api/chat/conversations", headers=alice).json()
    assert len(lst) == 1
    assert lst[0]["unread_count"] == 1
    assert lst[0]["last_message"]["content"] == "在吗"


def test_reinitiating_hidden_conversation_unhides_self(client):
    """我隐藏后再次 POST /conversations 应清掉自己的 hidden_at。"""
    alice = _auth_headers(client, "rhialice")
    _set_public(client, alice)
    bob = _auth_headers(client, "rhibob")
    _set_public(client, bob)
    _follow(client, alice, "rhibob")
    _follow(client, bob, "rhialice")
    conv = _start_conv(client, alice, "rhibob")

    assert client.delete(f"/api/chat/conversations/{conv['id']}", headers=alice).status_code == 204
    assert client.get("/api/chat/conversations", headers=alice).json() == []

    again = _start_conv(client, alice, "rhibob")
    assert again["id"] == conv["id"]
    lst = client.get("/api/chat/conversations", headers=alice).json()
    assert len(lst) == 1


# ---------------------------------------------------------------------------
# 游标分页
# ---------------------------------------------------------------------------


def test_cursor_pagination_returns_older_messages(client):
    alice = _auth_headers(client, "pgalice")
    _set_public(client, alice)
    bob = _auth_headers(client, "pgbob")
    _set_public(client, bob)
    _follow(client, bob, "pgalice")
    _follow(client, alice, "pgbob")
    conv = _start_conv(client, bob, "pgalice")

    # 直接批量插入 12 条（避开频控），使用 bob 的身份发送前 6 条 + alice 发送后 6 条
    # 为了绕开频控，这里让 alice/bob 交替发；每个用户 6 条，仍在 60/min 之内
    # 但同会话 2/秒 会卡；改为等待 → 太慢，直接调 service 层写库
    from back.database import SessionLocal
    from back.models import DirectMessage

    with SessionLocal() as s:
        # 找到 bob 的 user_id
        from back.models import User
        from sqlalchemy import select

        bob_id = s.scalar(select(User.id).where(User.username == "pgbob"))
        alice_id = s.scalar(select(User.id).where(User.username == "pgalice"))
        for i in range(12):
            s.add(
                DirectMessage(
                    conversation_id=conv["id"],
                    sender_id=bob_id if i % 2 == 0 else alice_id,
                    content=f"m{i}",
                )
            )
        s.commit()

    # 第一次拉：默认 limit=50 → 全部 12 条按 id 正序
    page1 = client.get(f"/api/chat/conversations/{conv['id']}/messages", headers=alice).json()
    assert len(page1) == 12
    assert [m["content"] for m in page1] == [f"m{i}" for i in range(12)]

    # limit=5 → 最新 5 条正序（m7..m11）
    page_latest_5 = client.get(
        f"/api/chat/conversations/{conv['id']}/messages?limit=5", headers=alice
    ).json()
    assert [m["content"] for m in page_latest_5] == ["m7", "m8", "m9", "m10", "m11"]

    # before_id = page_latest_5[0].id → 上一页 5 条（m2..m6）
    before = page_latest_5[0]["id"]
    page_prev = client.get(
        f"/api/chat/conversations/{conv['id']}/messages?before_id={before}&limit=5",
        headers=alice,
    ).json()
    assert [m["content"] for m in page_prev] == ["m2", "m3", "m4", "m5", "m6"]

    # 再往前翻 → m0, m1
    before2 = page_prev[0]["id"]
    page_oldest = client.get(
        f"/api/chat/conversations/{conv['id']}/messages?before_id={before2}&limit=5",
        headers=alice,
    ).json()
    assert [m["content"] for m in page_oldest] == ["m0", "m1"]


# ---------------------------------------------------------------------------
# SSE /stream 与 ChatHub
# ---------------------------------------------------------------------------
#
# 注：SSE 端点本身的鉴权（401）已由 test_chat_endpoints_require_auth 覆盖；
# 端到端流式行为（连接 → 收 connected 帧 → 收 message.new 帧 → 断开）
# 用 TestClient 会因无限生成器阻塞上下文退出，改由 P1 阶段 curl 手动验证。
# 下面三个测试直接对 ChatHub 做单元验证，覆盖同步线程 publish → 异步订阅者
# 收到的核心链路。


def test_chat_hub_publish_reaches_async_subscriber():
    """ChatHub 单元测试：同步线程 publish 应能被异步订阅者收到。"""

    async def scenario() -> dict:
        hub = ChatHub()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        loop = asyncio.get_running_loop()
        hub.register(42, queue, loop)
        assert hub.subscriber_count(42) == 1

        # 从另一个线程模拟同步 REST 端点调用 publish
        def worker():
            time.sleep(0.05)
            hub.publish(42, {"type": "message.new", "conversation_id": 7, "content": "hi"})

        t = threading.Thread(target=worker)
        t.start()
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        t.join()

        hub.unregister(42, queue)
        assert hub.subscriber_count(42) == 0
        return event

    received = asyncio.run(scenario())
    assert received["type"] == "message.new"
    assert received["conversation_id"] == 7


def test_chat_hub_publish_without_subscribers_is_noop():
    """无订阅者时 publish 应静默 no-op，不抛异常。"""
    hub = ChatHub()
    hub.publish(999, {"type": "message.new"})  # 不应抛
    assert hub.subscriber_count(999) == 0


def test_chat_hub_drops_oldest_when_queue_full():
    """队列满时应丢最旧一条，保证 publish 永不阻塞。"""

    async def scenario():
        hub = ChatHub()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        loop = asyncio.get_running_loop()
        hub.register(1, queue, loop)
        # 直接同步 publish 3 次；queue 应保留最后 2 条
        hub.publish(1, {"n": 1})
        hub.publish(1, {"n": 2})
        hub.publish(1, {"n": 3})
        # 让 loop 处理完 call_soon_threadsafe 调度的回调
        await asyncio.sleep(0.05)
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        hub.unregister(1, queue)
        return items

    items = asyncio.run(scenario())
    assert [i["n"] for i in items] == [2, 3]
