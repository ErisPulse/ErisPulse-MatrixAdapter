"""nio AsyncClient 封装：端到端加密会话管理

本模块将 matrix-nio 的 AsyncClient 适配为 ErisPulse Matrix 适配器的 E2EE 引擎：
- 复用适配器已登录的 access_token / device_id（restore_login）
- sync() 自动完成设备密钥上传、OTK 补充、to-device 收发、Megolm 解密，
  并将响应转换为与手写 REST /sync 完全相同的 dict 结构（事件取 .source），
  使 Core._process_sync_response 无需感知 nio 的存在
- 发送侧通过 room_send 在加密房间自动分享会话并加密
"""

import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from nio import (
        AsyncClient,
        AsyncClientConfig,
        JoinResponse,
        RoomSendResponse,
        UploadResponse,
    )
    from nio.events import BadEvent, MegolmEvent, UnknownBadEvent
    from nio.responses import SyncError, SyncResponse

    NIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    NIO_AVAILABLE = False


class NioSessionError(Exception):
    """NioSession 操作失败（携带错误消息与可选 HTTP 状态码）"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class NioSession:
    """单账户的 nio E2EE 会话（生命周期由 Core 管理）"""

    def __init__(
        self,
        account_name: str,
        homeserver: str,
        user_id: str,
        device_id: str,
        store_path: str,
        trust_all_devices: bool = True,
        logger=None,
    ):
        if not NIO_AVAILABLE:
            raise NioSessionError("matrix-nio is not installed")
        self._account_name = account_name
        self._homeserver = homeserver.rstrip("/")
        self._user_id = user_id
        self._device_id = device_id
        self._store_path = store_path
        self._trust_all_devices = trust_all_devices
        self._logger = logger
        self._client: Optional[AsyncClient] = None

    # ---- 生命周期 ----

    def _log(self, level: str, message: str):
        if self._logger is not None:
            getattr(self._logger, level)(message)

    async def start(self, access_token: str) -> bool:
        """构造 AsyncClient 并以现有凭据恢复登录；成功返回 True"""
        if self._client is not None:
            return True
        try:
            Path(self._store_path).mkdir(parents=True, exist_ok=True)
            self._client = AsyncClient(
                self._homeserver,
                self._user_id,
                device_id=self._device_id,
                store_path=self._store_path,
                config=AsyncClientConfig(encryption_enabled=True),
            )
            self._client.restore_login(self._user_id, self._device_id, access_token)
            if self._trust_all_devices:
                self._trust_own_devices()
            return True
        except Exception as e:
            self._log("error", f"[matrix] 账户 {self._account_name} 初始化 E2EE 会话失败: {e}")
            await self.close()
            return False

    async def close(self):
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def started(self) -> bool:
        return self._client is not None

    # ---- 信任管理 ----

    def _trust_own_devices(self):
        """自动信任本用户已知的其他设备（bot 场景惯例，替代缺失的 trust_own_devices）"""
        try:
            client = self._client
            for device in client.device_store.active_user_devices(client.user_id):
                if device.deleted:
                    continue
                if not client.verify_device(device):
                    self._log("debug", f"[matrix] 已信任设备 {device.id}")
        except Exception as e:
            self._log("warning", f"[matrix] 信任自有设备失败: {e}")

    # ---- 同步 ----

    async def sync(self, timeout_ms: int = 30000) -> Optional[dict]:
        """执行一次同步，返回与 REST /sync 响应相同结构的 dict；失败返回 None

        nio 在内部自动处理 E2EE（密钥上传/补充、to-device、解密），
        解密失败的 Megolm 事件被跳过并记日志。
        """
        client = self._client
        if client is None:
            return None
        try:
            resp = await client.sync(timeout_ms)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise NioSessionError(f"sync request failed: {e}") from e

        if isinstance(resp, SyncError):
            status = getattr(resp, "status_code", 0) or 0
            raise NioSessionError(
                f"sync failed: {getattr(resp, 'message', '') or resp.__class__.__name__} (HTTP {status})",
                status_code=status,
            )

        return self._convert_sync_response(resp)

    def _convert_sync_response(self, resp: "SyncResponse") -> dict:
        """将 nio SyncResponse 转换为 REST /sync 的 dict 结构"""
        client = self._client
        join_rooms: Dict[str, dict] = {}
        skip_count = 0
        for room_id, room_info in resp.rooms.join.items():
            room = client.rooms.get(room_id) if client else None
            room_encrypted = bool(room and getattr(room, "encrypted", False))
            events: List[dict] = []
            for event in room_info.timeline.events:
                if isinstance(event, (MegolmEvent, BadEvent, UnknownBadEvent)):
                    # MegolmEvent = 解密失败（nio 保留原样）；BadEvent = 格式非法
                    skip_count += 1
                    self._log(
                        "warning",
                        f"[matrix] 账户 {self._account_name} 事件解密失败或格式非法，已跳过: "
                        f"{getattr(event, 'event_id', '?')} (room {room_id})",
                    )
                    continue
                events.append(self._event_to_dict(event, room_id, room_encrypted))
            join_rooms[room_id] = {"timeline": {"events": events}}

        invite_rooms: Dict[str, dict] = {}
        for room_id, invite_info in resp.rooms.invite.items():
            invite_events = []
            # invite_state 在 nio 中即事件对象列表
            for event in invite_info.invite_state:
                source = getattr(event, "source", None)
                if isinstance(source, dict):
                    source.setdefault("room_id", room_id)
                    invite_events.append(source)
            invite_rooms[room_id] = {"invite_state": {"events": invite_events}}

        return {
            "next_batch": resp.next_batch,
            "rooms": {"join": join_rooms, "invite": invite_rooms},
        }

    @staticmethod
    def _event_to_dict(event, room_id: str, room_encrypted: bool = False) -> dict:
        source = dict(getattr(event, "source", {}) or {})
        source.setdefault("room_id", room_id)
        if room_encrypted:
            # 供 MatrixEventMixin.is_encrypted() 判断事件来自加密房间
            source["matrix_e2ee"] = True
        return source

    @property
    def next_batch(self) -> str:
        client = self._client
        return getattr(client, "next_batch", "") if client else ""

    # ---- 加密状态与发送 ----

    def is_room_encrypted(self, room_id: str) -> bool:
        """目标房间是否为加密房间（依赖 nio 的房间缓存，需至少完成一次同步）"""
        client = self._client
        if client is None:
            return False
        room = client.rooms.get(room_id)
        return bool(room and getattr(room, "encrypted", False))

    async def send_event(
        self, room_id: str, event_type: str, content: dict
    ) -> Tuple[bool, str, str]:
        """向房间发送事件（加密房间自动加密）

        返回 (ok, event_id, error)。
        """
        client = self._client
        if client is None:
            return False, "", "E2EE session not started"
        try:
            resp = await client.room_send(
                room_id,
                event_type,
                content,
                ignore_unverified_devices=self._trust_all_devices,
            )
        except Exception as e:
            return False, "", f"{type(e).__name__}: {e}"

        from nio.responses import RoomSendError

        if isinstance(resp, RoomSendError):
            return False, "", getattr(resp, "message", "") or resp.__class__.__name__
        if isinstance(resp, RoomSendResponse):
            return True, resp.event_id, ""
        return False, "", f"unexpected response: {resp.__class__.__name__}"

    # ---- 加密媒体 ----

    async def upload_media(
        self, file_bytes: bytes, content_type: str, filename: str = "", encrypt: bool = False
    ) -> Tuple[Optional[str], Optional[dict], str]:
        """上传媒体；encrypt=True 时上传端到端加密密文

        返回 (mxc_uri, file_dict_or_none, error)。加密上传的 file_dict
        （含 url/key/iv/hashes）应放入事件 content 的 "file" 字段替代 "url"。
        """
        client = self._client
        if client is None:
            return None, None, "E2EE session not started"
        try:
            resp, file_dict = await client.upload(
                lambda total, completed: file_bytes,
                content_type=content_type,
                filename=filename or None,
                encrypt=encrypt,
            )
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"

        from nio.responses import UploadError

        if isinstance(resp, UploadError):
            return None, None, getattr(resp, "message", "") or resp.__class__.__name__
        if isinstance(resp, UploadResponse):
            if encrypt and isinstance(file_dict, dict):
                # 组装为 Matrix 规范的 m.file.encrypted 结构（nio 只返回 key/iv/hashes）
                file_dict.setdefault("v", "v2")
                file_dict.setdefault("url", resp.content_uri)
            return resp.content_uri, file_dict or None, ""
        return None, None, f"unexpected response: {resp.__class__.__name__}"

    async def join_room(self, room_id: str) -> bool:
        """加入房间（与 REST join 等价，经 nio 以保持同一会话状态）"""
        client = self._client
        if client is None:
            return False
        try:
            resp = await client.join(room_id)
            return isinstance(resp, JoinResponse)
        except Exception:
            return False
