"""E2EE 单元测试（离线，不依赖真实 Matrix 服务器）

覆盖：nio 会话封装的 sync 响应转换、device_id 解析优先级与持久化、
会话初始化降级路径、sync/发送分支选择、加密媒体加解密往返、
Converter 的 m.file.encrypted 解析、is_encrypted 事件标记。

运行：python test/test_e2ee.py
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from MatrixAdapter import __version__  # noqa: E402
from MatrixAdapter.Core import MatrixAccountConfig, MatrixAdapter  # noqa: E402
from MatrixAdapter.NioSession import NIO_AVAILABLE, NioSession, NioSessionError  # noqa: E402
from MatrixAdapter.Converter import MatrixConverter  # noqa: E402

RESULTS = []


def check(name: str, cond: bool, detail: str = ""):
    RESULTS.append((name, cond, detail))
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail}" if (detail and not cond) else ""))


# ============================================================================
# 测试辅助
# ============================================================================

class FakeSession:
    """用于注入 _nio_sessions 的假 E2EE 会话"""

    def __init__(self, encrypted_rooms=None, sync_data=None, sync_error=None,
                 send_result=(True, "$evt_test", "")):
        self.started = True
        self.encrypted_rooms = encrypted_rooms or set()
        self.sync_data = sync_data
        self.sync_error = sync_error
        self.send_result = send_result
        self.send_calls = []
        self.next_batch = "nb_1"

    def is_room_encrypted(self, room_id):
        return room_id in self.encrypted_rooms

    async def sync(self, timeout_ms=30000):
        if self.sync_error:
            raise self.sync_error
        return self.sync_data

    async def send_event(self, room_id, event_type, content):
        self.send_calls.append((room_id, event_type, dict(content)))
        return self.send_result


def make_account(**overrides) -> MatrixAccountConfig:
    base = dict(
        homeserver="https://matrix.example.org",
        access_token="tok_test",
        user_id="@bot:matrix.example.org",
        password="",
    )
    base.update(overrides)
    return MatrixAccountConfig(**base)


def make_adapter() -> MatrixAdapter:
    adapter = MatrixAdapter()
    account = make_account()
    # accounts 走框架 setter（同步写入配置存储）；enabled_accounts 为派生只读属性
    adapter.accounts = {"default": account}
    adapter._account_runtime["default"] = {
        "bot_id": "@bot:matrix.example.org",
        "access_token": "tok_test",
        "_next_batch": None,
        "_dm_rooms": {},
        "device_id": "",
    }
    return adapter


def make_nio_sync_response():
    """构造真实的 nio SyncResponse 对象（含明文事件 + 解密失败的 MegolmEvent）"""
    from nio.events import RoomMessageText, MegolmEvent
    from nio.responses import (
        Rooms, RoomInfo, Timeline, InviteInfo, InviteEvent, SyncResponse,
        DeviceOneTimeKeyCount, DeviceList,
    )

    plain_event = RoomMessageText(
        {
            "type": "m.room.message",
            "sender": "@alice:matrix.example.org",
            "event_id": "$plain1",
            "origin_server_ts": 1700000000000,
            "content": {"msgtype": "m.text", "body": "hello"},
        },
        "hello", None, None,
    )
    megolm_event = MegolmEvent.from_dict({
        "type": "m.room.encrypted",
        "sender": "@alice:matrix.example.org",
        "event_id": "$undecryptable",
        "origin_server_ts": 1700000001000,
        "content": {
            "algorithm": "m.megolm.v1.aes-sha2",
            "ciphertext": "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "sender_key": "KEY",
            "device_id": "DEV",
            "session_id": "SESSION",
        },
    })
    timeline = Timeline(
        events=[plain_event, megolm_event], limited=False, prev_batch="pb",
    )

    class _FakeRoom:
        encrypted = True

    class _FakeClient:
        rooms = {"!enc:matrix.example.org": _FakeRoom()}

    resp = SyncResponse(
        next_batch="nb_2",
        rooms=Rooms(
            invite={"!inv:matrix.example.org": InviteInfo(
                invite_state=[InviteEvent({
                    "type": "m.room.member",
                    "sender": "@alice:matrix.example.org",
                    "state_key": "@bot:matrix.example.org",
                    "content": {"membership": "invite"},
                }, "@alice:matrix.example.org")],
            )},
            join={"!enc:matrix.example.org": RoomInfo(
                timeline=timeline, state=[], ephemeral=[], account_data=[], summary=None,
                unread_notifications=None, unread_thread_notifications=None,
            )},
            leave={},
        ),
        device_key_count=DeviceOneTimeKeyCount(signed_curve25519=50, curve25519=0),
        device_list=DeviceList(changed=[], left=[]),
        to_device_events=[],
        presence_events=[],
    )
    return resp, _FakeClient


# ============================================================================
# 测试用例
# ============================================================================

async def test_version_and_dependencies():
    check("版本为 4.3.0", __version__ == "4.3.0", __version__)
    check("matrix-nio 可用（E2EE 依赖已安装）", NIO_AVAILABLE)


async def test_config_fields():
    acc = MatrixAccountConfig()
    check("encryption_enabled 默认关闭", acc.encryption_enabled is False)
    check("trust_all_devices 默认开启", acc.trust_all_devices is True)
    check("device_id 默认为空", acc.device_id == "")
    check("store_path 默认为空（运行时落到 data/matrix/<账户>）", acc.store_path == "")


async def test_nio_session_convert_sync():
    resp, fake_client = make_nio_sync_response()
    session = NioSession(
        account_name="default",
        homeserver="https://matrix.example.org",
        user_id="@bot:matrix.example.org",
        device_id="DEVTEST",
        store_path=tempfile.mkdtemp(),
    )
    session._client = fake_client  # 直接注入假 client，绕过网络
    try:
        data = session._convert_sync_response(resp)
    finally:
        shutil.rmtree(session._store_path, ignore_errors=True)

    events = data["rooms"]["join"]["!enc:matrix.example.org"]["timeline"]["events"]
    check("明文事件转换为 dict", isinstance(events[0], dict) and events[0]["content"]["body"] == "hello")
    check("解密失败的 Megolm 事件被跳过", len(events) == 1, f"got {len(events)}")
    check("事件保留 room_id", events[0].get("room_id") == "!enc:matrix.example.org")
    check("加密房间事件带 matrix_e2ee 标记", events[0].get("matrix_e2ee") is True)
    check("next_batch 透传", data["next_batch"] == "nb_2")
    invite = data["rooms"]["invite"].get("!inv:matrix.example.org")
    check("邀请房状态事件转换",
          bool(invite and invite["invite_state"]["events"][0]["type"] == "m.room.member"))


async def test_device_id_priority():
    adapter = make_adapter()
    account = adapter.accounts["default"]
    store_dir = tempfile.mkdtemp()
    account.store_path = store_dir
    try:
        # 1) runtime（登录响应）最低优先级
        adapter._account_runtime["default"]["device_id"] = "$from_login"
        check("无配置无持久化时取登录响应",
              adapter._resolve_device_id("default", account) == "$from_login")

        # 2) 持久化覆盖 runtime
        (Path(store_dir) / "device.json").write_text('{"device_id": "$persisted"}', encoding="utf-8")
        check("持久化值优先于登录响应",
              adapter._resolve_device_id("default", account) == "$persisted")

        # 3) 配置项最高
        account.device_id = "$configured"
        check("配置项优先级最高",
              adapter._resolve_device_id("default", account) == "$configured")

        # 持久化 round-trip
        account.device_id = ""
        adapter._persist_device_id("default", account, "$new_dev")
        check("device_id 持久化写读一致",
              adapter._load_persisted_device_id("default", account) == "$new_dev")
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)


async def test_init_e2ee_session_degradation():
    from MatrixAdapter import Core as core_mod

    adapter = make_adapter()
    account = adapter.accounts["default"]
    account.encryption_enabled = True
    account.store_path = tempfile.mkdtemp()

    # 1) nio 缺失 → 仅警告，不建会话
    orig = core_mod.NIO_AVAILABLE
    core_mod.NIO_AVAILABLE = False
    try:
        await adapter._init_e2ee_session("default", account)
        check("nio 缺失时降级为明文（无会话建立）", not adapter._nio_sessions)
    finally:
        core_mod.NIO_AVAILABLE = orig

    # 2) nio 可用但无 device_id → 不建会话
    await adapter._init_e2ee_session("default", account)
    check("无 device_id 时不建立 E2EE 会话", not adapter._nio_sessions)

    # 3) 有 device_id → 真实建立会话（restore_login 不需要网络）
    adapter._account_runtime["default"]["device_id"] = "DEVTEST123"
    await adapter._init_e2ee_session("default", account)
    session = adapter._nio_sessions.get("default")
    ok = session is not None and session.started
    check("device_id 就绪时成功建立 E2EE 会话", ok)
    if ok:
        check("活跃会话可经 _nio_session_for 获取", adapter._nio_session_for("default") is session)
        await adapter._teardown_e2ee_session("default")
        check("会话可清理", adapter._nio_session_for("default") is None)
    shutil.rmtree(account.store_path, ignore_errors=True)


async def test_do_sync_request_branches():
    adapter = make_adapter()

    # E2EE 活跃 → 走 nio
    fake = FakeSession(sync_data={"next_batch": "nb_x", "rooms": {"join": {}, "invite": {}}})
    adapter._nio_sessions["default"] = fake
    result = await adapter._do_sync_request("default")
    check("E2EE 活跃时 sync 走 nio 会话", result.get("status") == "ok" and result.get("data") == fake.sync_data)

    # nio 报错 → 统一失败结构
    fake.sync_error = NioSessionError("M_UNKNOWN_TOKEN", status_code=401)
    result = await adapter._do_sync_request("default")
    check("nio 同步失败转为 failed 结构",
          result.get("status") == "failed" and result.get("http_status") == 401)

    # 无会话 → 走 REST call_api
    adapter._nio_sessions.clear()
    async def fake_call_api(endpoint, method="POST", _account_id=None, **kw):
        return {"status": "ok", "data": {"next_batch": "rest_nb", "rooms": {}}}
    adapter.call_api = fake_call_api
    result = await adapter._do_sync_request("default")
    check("无会话时回退 REST 长轮询", result.get("data", {}).get("next_batch") == "rest_nb")


async def test_send_encrypted_branch():
    adapter = make_adapter()
    fake = FakeSession(encrypted_rooms={"!enc:matrix.example.org"})
    adapter._nio_sessions["default"] = fake
    adapter.call_api = async_raise = None  # 占位防误用：加密路径不应触达 REST

    async def _should_not_call(*a, **kw):
        raise AssertionError("encrypted path must not call REST call_api")

    adapter.call_api = _should_not_call

    send = MatrixAdapter.Send(adapter, "group", "!enc:matrix.example.org", "default")
    result = await send._do_send_raw_ob12([{"type": "text", "data": {"text": "secret"}}])

    check("加密房发送返回 ok", result.get("status") == "ok", str(result))
    check("加密房发送返回 event_id 作为 message_id", result.get("message_id") == "$evt_test")
    check("加密发送经 nio 会话（m.room.message）",
          len(fake.send_calls) == 1 and fake.send_calls[0][1] == "m.room.message")
    if fake.send_calls:
        content = fake.send_calls[0][2]
        check("加密发送保留明文 content 结构（加密由 nio 完成）",
              content.get("body") == "secret" and content.get("msgtype") == "m.text")


async def test_send_plaintext_branch():
    adapter = make_adapter()
    fake = FakeSession(encrypted_rooms=set())  # 房间未加密
    adapter._nio_sessions["default"] = fake

    async def fake_call_api(endpoint, method="POST", _account_id=None, **kw):
        return {"status": "ok", "data": {"event_id": "$rest_evt"}, "message_id": "$rest_evt"}

    adapter.call_api = fake_call_api
    send = MatrixAdapter.Send(adapter, "group", "!plain:matrix.example.org", "default")
    result = await send._do_send_raw_ob12([{"type": "text", "data": {"text": "hi"}}])
    check("非加密房发送走 REST", result.get("message_id") == "$rest_evt" and not fake.send_calls)


async def test_decrypt_media_roundtrip():
    if not NIO_AVAILABLE:
        check("媒体加解密往返（跳过：nio 未安装）", True)
        return
    from nio.crypto import encrypt_attachment

    plaintext = b"binary image data " * 100
    ciphertext, file_dict = encrypt_attachment(plaintext)
    decrypted = MatrixAdapter.decrypt_media(
        ciphertext,
        key=file_dict["key"]["k"],
        iv=file_dict["iv"],
        hash_value=file_dict["hashes"]["sha256"],
    )
    check("加密媒体加解密往返一致", decrypted == plaintext)


async def test_converter_encrypted_file():
    converter = MatrixConverter(bot_user_id="@bot:matrix.example.org")
    jwk = {
        "v": "v2",
        "url": "mxc://matrix.example.org/abc",
        "key": {"alg": "A256CTR", "kty": "oct", "k": "KEY", "ext": True, "key_ops": ["decrypt"]},
        "iv": "IV",
        "hashes": {"sha256": "HASH"},
    }
    event = {
        "type": "m.room.message",
        "sender": "@alice:matrix.example.org",
        "event_id": "$media1",
        "content": {"msgtype": "m.image", "body": "pic.png", "file": jwk},
    }
    ob = converter.convert(event, room_id="!enc:matrix.example.org", is_dm=False)
    seg = ob["message"][0]
    check("加密媒体段携带 matrix_encrypted_file",
          seg["data"].get("matrix_encrypted_file") == jwk)
    check("加密媒体段标记 encrypted", seg["data"].get("encrypted") is True)
    check("加密媒体段 url 指向密文 mxc", seg["data"].get("url") == "mxc://matrix.example.org/abc")

    # 明文媒体不受影响
    event_plain = dict(event, event_id="$media2", content={"msgtype": "m.image", "body": "p.png", "url": "mxc://x/y"})
    seg2 = converter.convert(event_plain, room_id="!r:x", is_dm=False)["message"][0]
    check("明文媒体段保持 matrix_mxc 语义", seg2["data"].get("matrix_mxc") == "mxc://x/y" and "encrypted" not in seg2["data"])


async def test_mixin_is_encrypted():
    from MatrixAdapter.Core import MatrixEventMixin

    class E(dict, MatrixEventMixin):
        pass

    check("is_encrypted 识别 matrix_e2ee 标记",
          E({"matrix_raw": {"matrix_e2ee": True}}).is_encrypted() is True)
    check("is_encrypted 对普通事件返回 False",
          E({"matrix_raw": {}}).is_encrypted() is False)


# ============================================================================
# 入口
# ============================================================================

async def main():
    for fn in [
        test_version_and_dependencies,
        test_config_fields,
        test_nio_session_convert_sync,
        test_device_id_priority,
        test_init_e2ee_session_degradation,
        test_do_sync_request_branches,
        test_send_encrypted_branch,
        test_send_plaintext_branch,
        test_decrypt_media_roundtrip,
        test_converter_encrypted_file,
        test_mixin_is_encrypted,
    ]:
        print(f"\n=== {fn.__name__} ===")
        try:
            await fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} 未抛出异常", False, repr(e))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 50}\nE2EE 测试结果: {passed}/{total} 通过")
    if passed != total:
        print("失败项：")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
