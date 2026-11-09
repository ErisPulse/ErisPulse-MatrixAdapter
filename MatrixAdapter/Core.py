import asyncio
import base64
import html as _html
import mimetypes
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from ErisPulse.Core import client
from ErisPulse.Core.Bases.adapter import BaseAdapter
from ErisPulse.Core.Bases import BotAccountConfig
from ErisPulse.Core.Event import register_event_mixin, unregister_platform_event_methods
from ErisPulse.Core.i18n import i18n

from .Converter import MatrixConverter
from .NioSession import NIO_AVAILABLE, NioSession, NioSessionError


# 常见媒体格式的魔数前缀
_MEDIA_MAGIC_PREFIXES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"OggS",
    b"fLaC",
    b"ID3",
    b"RIFF",
    b"%PDF",
    b"PK\x03\x04",
    b"\x1a\x45\xdf\xa3",
)


def _looks_like_media_bytes(data: bytes) -> bool:
    """按魔数判断 bytes 是否为常见媒体/文件格式"""
    if any(data.startswith(prefix) for prefix in _MEDIA_MAGIC_PREFIXES):
        return True
    if len(data) > 12 and data[4:8] == b"ftyp":
        return True
    return len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0


def _decode_base64_media(value: str) -> Optional[bytes]:
    """标准 base64 字符串解码为 bytes（解码结果须通过魔数校验）；不满足返回 None"""
    if not value or value.strip() != value:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception:
        return None
    return decoded if _looks_like_media_bytes(decoded) else None


def _decode_data_uri(value: str) -> Optional[bytes]:
    """data: URI 解码为 bytes；格式不合法或解码失败返回 None"""
    head, sep, payload = value.partition(",")
    if not sep or not head.startswith("data:"):
        return None
    if head.endswith(";base64"):
        try:
            return base64.b64decode(payload, validate=True)
        except Exception:
            return None
    return unquote(payload).encode("utf-8")


def _geo_uri_from_data(data: dict) -> str:
    """由 OB12 location 段 data 构建 geo URI（优先平台扩展 matrix_geo_uri）"""
    custom = str(data.get("matrix_geo_uri") or "").strip()
    if custom:
        return custom
    try:
        latitude = float(data.get("latitude", 0.0) or 0.0)
    except (TypeError, ValueError):
        latitude = 0.0
    try:
        longitude = float(data.get("longitude", 0.0) or 0.0)
    except (TypeError, ValueError):
        longitude = 0.0
    return f"geo:{latitude},{longitude}"


def _build_location_content(data: dict, fallback_body: str = " ") -> dict:
    """OB12 location 段 → Matrix m.location 事件 content"""
    body = str(data.get("text") or data.get("title") or "").strip() or fallback_body
    return {
        "msgtype": "m.location",
        "body": body,
        "geo_uri": _geo_uri_from_data(data),
    }


def _forward_node_text(node: Dict) -> str:
    """提取合并转发节点 content 中全部 text 段文本（段间直接连接）"""
    data = node.get("data") or {}
    return "".join(
        str(seg.get("data", {}).get("text") or "")
        for seg in (data.get("content") or [])
        if isinstance(seg, dict) and seg.get("type") == "text"
    )


def _forward_nodes_plain(nodes, separator: str = "\n") -> str:
    """合并转发节点降级纯文本：每条「昵称: 正文」"""
    lines = []
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if not isinstance(node, dict):
            continue
        data = node.get("data") or {}
        nickname = str(data.get("nickname") or "").strip()
        text = _forward_node_text(node).strip()
        if nickname and text:
            lines.append(f"{nickname}: {text}")
        elif text:
            lines.append(text)
    return separator.join(lines)


def _build_forward_matrix_html(nodes, title: str = "合并聊天记录") -> str:
    """渲染合并转发为 Matrix formatted_body（白名单标签：p/strong/hr/blockquote/br）

    Matrix HTML 白名单会剥离 details/summary 与内联样式，故用 blockquote 引用块形态。
    """
    blocks = []
    for node in nodes if isinstance(nodes, list) else [nodes]:
        if not isinstance(node, dict):
            continue
        data = node.get("data") or {}
        nickname = str(data.get("nickname") or "").strip()
        text = _forward_node_text(node).strip()
        if not text:
            continue
        header = f"<strong>{_html.escape(nickname)}</strong><br/>" if nickname else ""
        body = _html.escape(text).replace("\n", "<br/>")
        blocks.append(f"<blockquote>{header}{body}</blockquote>")
    if not blocks:
        return ""
    return (
        f"<p><strong>{_html.escape(title)}</strong></p>"
        + "<hr/>".join(blocks)
    )

try:
    from ErisPulse.runtime.tasks import spawn_background
except ImportError:  # pragma: no cover
    spawn_background = None

__version__ = "4.6.1"

# 软依赖的框架最低版本（运行时检测，仅提示不强制）
MIN_FRAMEWORK_VERSION = (2, 7, 1)


# ============================================================================
# 配置声明（多账户）
# ============================================================================
@dataclass
class MatrixAccountConfig(BotAccountConfig):
    homeserver: str = field(
        default="https://matrix.org",
        metadata={
            "description": {"i18n": "matrix.homeserver", "default": "Matrix 服务器地址"},
            "required": True,
            "ui": {
                "widget": "text",
                "group": "connection",
                "order": 1,
                "placeholder": {"i18n": "matrix.homeserver.ph", "default": "https://matrix.org"},
            },
        },
    )
    access_token: str = field(
        default="",
        metadata={
            "description": {"i18n": "matrix.access_token", "default": "Access Token（替代 user_id/password）"},
            "secret": True,
            "ui": {"widget": "password", "group": "basic", "order": 2},
        },
    )
    user_id: str = field(
        default="",
        metadata={
            "description": {"i18n": "matrix.user_id", "default": "用户名（用于密码登录）"},
            "ui": {"widget": "text", "group": "basic", "order": 3},
        },
    )
    password: str = field(
        default="",
        metadata={
            "description": {"i18n": "matrix.password", "default": "密码（用于密码登录）"},
            "secret": True,
            "ui": {
                "widget": "password",
                "group": "basic",
                "order": 4,
                "disabled_if": {"field": "access_token", "op": "truthy"},
            },
        },
    )
    auto_accept_invites: bool = field(
        default=True,
        metadata={
            "description": {"i18n": "matrix.auto_accept_invites", "default": "自动接受房间邀请"},
            "ui": {"widget": "switch", "group": "behavior", "order": 5},
        },
    )
    encryption_enabled: bool = field(
        default=False,
        metadata={
            "description": {"i18n": "matrix.encryption_enabled", "default": "启用端到端加密（加密房间消息自动加解密）"},
            "ui": {"widget": "switch", "group": "encryption", "order": 6},
        },
    )
    trust_all_devices: bool = field(
        default=True,
        metadata={
            "description": {"i18n": "matrix.trust_all_devices", "default": "自动信任未验证设备（关闭后不向未验证设备分发消息密钥）"},
            "ui": {
                "widget": "switch",
                "group": "encryption",
                "order": 7,
                "show_if": {"field": "encryption_enabled", "op": "eq", "value": True},
            },
        },
    )
    device_id: str = field(
        default="",
        metadata={
            "description": {"i18n": "matrix.device_id", "default": "设备 ID（通常自动获取，仅在服务器不支持时手动指定）"},
            "ui": {
                "widget": "text",
                "group": "encryption",
                "order": 8,
                "show_if": {"field": "encryption_enabled", "op": "eq", "value": True},
            },
        },
    )
    store_path: str = field(
        default="",
        metadata={
            "description": {"i18n": "matrix.store_path", "default": "加密会话存储目录（默认 data/matrix/<账户名>，请勿删除）"},
            "ui": {
                "widget": "text",
                "group": "encryption",
                "order": 9,
                "placeholder": {"i18n": "matrix.store_path.ph", "default": "data/matrix/<账户名>"},
                "show_if": {"field": "encryption_enabled", "op": "eq", "value": True},
            },
        },
    )


MatrixAccountConfig._schema_meta = {
    "group_labels": {
        "basic": {"i18n": "matrix.group.basic", "default": "基本设置"},
        "connection": {"i18n": "matrix.group.connection", "default": "连接设置"},
        "behavior": {"i18n": "matrix.group.behavior", "default": "行为设置"},
        "encryption": {"i18n": "matrix.group.encryption", "default": "端到端加密"},
    }
}  # type: ignore[attr-defined]


# ============================================================================
# 事件 Mixin（平台扩展方法）
# ============================================================================
class MatrixEventMixin:
    def get_room_id(self) -> str:
        return self.get("matrix_room_id", self.get("matrix_raw", {}).get("room_id", ""))

    def get_matrix_event_type(self) -> str:
        return self.get("matrix_raw", {}).get("type", "")

    def get_matrix_sender(self) -> str:
        return self.get("matrix_raw", {}).get("sender", "")

    def get_reaction_key(self) -> str:
        return self.get("matrix_reaction_key", "")

    def is_edited(self) -> bool:
        return self.get("matrix_edit", False)

    def is_encrypted(self) -> bool:
        return bool(self.get("matrix_raw", {}).get("matrix_e2ee", False))

    def is_notice(self) -> bool:
        raw = self.get("matrix_raw", {})
        content = raw.get("content", {})
        return content.get("msgtype") == "m.notice"


register_event_mixin("matrix", MatrixEventMixin)


# ============================================================================
# 适配器主类
# ============================================================================
class MatrixAdapter(BaseAdapter):
    """Matrix 协议适配器（多账户，Long Polling /sync）"""

    AccountConfigClass = MatrixAccountConfig

    # ---- Send 类 ----
    class Send(BaseAdapter.Send):
        # NOTE: 不自定义 At / AtAll / Reply —— SendDSL 基类已内置
        # （它们把数据写入 self._at_user_ids / self._at_all / self._reply_message_id）

        def Text(self, text: str):
            return self.Raw_ob12([{"type": "text", "data": {"text": text}}])

        def Image(self, file):
            return self.Raw_ob12([{"type": "image", "data": {"file": file}}])

        def Voice(self, file):
            return self.Raw_ob12([{"type": "audio", "data": {"file": file}}])

        def Video(self, file):
            return self.Raw_ob12([{"type": "video", "data": {"file": file}}])

        def File(self, file, filename: str = ""):
            data = {"file": file}
            if filename:
                data["filename"] = filename
            return self.Raw_ob12([{"type": "file", "data": data}])

        def Notice(self, text: str):
            return self.Raw_ob12([{"type": "notice", "data": {"text": text}}])

        def Html(self, html: str, fallback: str = ""):
            return self.Raw_ob12(
                [{"type": "html", "data": {"html": html, "fallback": fallback}}]
            )

        # 标准名别名（HTML 为规范拼写，Html 保留兼容既有模块）
        HTML = Html

        def MergeForward(self, nodes, title: str = "合并聊天记录"):
            """发送合并转发消息（Matrix formatted_body：标题 + 每条 blockquote 引用块）

            :param nodes: go-cqhttp 扩展的 OneBot v11 合并转发节点数组（或单节点 dict）
            :param title: 标题
            :return: asyncio.Task
            """
            if isinstance(nodes, dict):
                nodes = [nodes]
            return asyncio.create_task(
                self._do_send_merge_forward(
                    [n for n in nodes if isinstance(n, dict)], title
                )
            )

        async def _do_send_merge_forward(self, nodes, title: str = "合并聊天记录"):
            html = _build_forward_matrix_html(nodes, title)
            if not html:
                return self._adapter.make_error(
                    retcode=10006, message="MergeForward: 节点无有效文本内容"
                )
            return await self._do_send_raw_ob12(
                [
                    {
                        "type": "html",
                        "data": {
                            "html": html,
                            "fallback": _forward_nodes_plain(nodes),
                        },
                    }
                ]
            )

        def Raw_ob12(self, message: List[Dict], **kwargs):
            # node 段解析：纯 node 列表自动路由合并转发；混合列表降级为纯文本
            if message and all(
                isinstance(seg, dict) and seg.get("type") == "node" for seg in message
            ):
                return asyncio.create_task(
                    self._do_send_merge_forward([seg for seg in message])
                )
            message = [
                {"type": "text", "data": {"text": _forward_nodes_plain(seg)}}
                if isinstance(seg, dict) and seg.get("type") == "node"
                else seg
                for seg in message
            ]

            async def _send():
                return await self._do_send_raw_ob12(message, **kwargs)

            return asyncio.create_task(_send())

        async def _do_send_raw_ob12(self, message: List[Dict], **kwargs):
            text_parts = []
            html_parts = []
            media_file = None
            media_type = None
            media_filename = None
            is_notice = False
            fallback_text = ""
            segment_mentions = []
            location_data = None

            for segment in message:
                seg_type = segment.get("type")
                data = segment.get("data", {})

                if seg_type == "text":
                    text_parts.append(data.get("text", ""))
                elif seg_type == "notice":
                    is_notice = True
                    text_parts.append(data.get("text", ""))
                elif seg_type == "html":
                    html_parts.append(data.get("html", ""))
                    fallback_text = data.get("fallback", "")
                elif seg_type == "reply":
                    self._reply_message_id = data.get("message_id", "")
                elif seg_type == "mention":
                    user_id = data.get("user_id", "")
                    if user_id:
                        text_parts.append(user_id)
                        segment_mentions.append(user_id)
                elif seg_type in ("image", "voice", "audio", "video", "file"):
                    file = data.get("file", data.get("url", ""))
                    if file:
                        media_file = file
                        media_type = seg_type
                        if data.get("filename"):
                            media_filename = data["filename"]
                elif seg_type == "location":
                    location_data = data
                else:
                    self._adapter._get_logger().warning(f"未处理的消息段类型，已跳过: {seg_type}")

            # 基类内置修饰器：_at_user_ids / _at_all
            if self._at_user_ids:
                text_parts.insert(0, " ".join(self._at_user_ids) + " ")
            if self._at_all:
                text_parts.insert(0, "@room ")

            full_text = "".join(text_parts) or fallback_text or " "
            full_html = "".join(html_parts) if html_parts else None

            ctx = self.send_context
            account_id = ctx.get("account_id")
            # 多账户：解析当前发送所用账户（用于媒体上传/下载与发送）
            account_name, _ = self._adapter._resolve_account(account_id)
            # 目标解析：群发 "!room" 直用；私聊 "user" 反查 DM 房间表
            target_room, resolve_error = self._adapter._resolve_target_room(
                ctx.get("target_id"), account_name
            )
            if not target_room:
                return self._adapter.make_error(
                    retcode=10003, message=resolve_error, raw=None
                )
            # E2EE：目标房间为加密房间且会话就绪时，媒体加密上传 + 事件加密发送
            session = self._adapter._nio_session_for(account_name)
            room_encrypted = bool(session and target_room and session.is_room_encrypted(target_room))
            content = None

            async def _upload_bytes(file_bytes: bytes, content_type: str = None):
                """上传媒体；加密房间走端到端加密上传。返回 (mxc_uri, file_dict)"""
                if room_encrypted:
                    ct = content_type
                    if not ct:
                        ct = {
                            "image": "image/png",
                            "voice": "audio/ogg",
                            "audio": "audio/mpeg",
                            "video": "video/mp4",
                            "file": "application/octet-stream",
                        }.get(media_type, "application/octet-stream")
                    mxc_uri, file_dict, err = await session.upload_media(
                        file_bytes, ct, encrypt=True
                    )
                    if not mxc_uri:
                        self._adapter._get_logger().error(
                            i18n.t("matrix.e2ee_upload_failed", name=account_name, error=err, default="账户 {name} 加密媒体上传失败: {error}")
                        )
                        return None, None
                    return mxc_uri, file_dict
                mxc_uri = await self._adapter._upload_media(
                    account_name, file_bytes, media_type, content_type=content_type
                )
                return mxc_uri, None

            if media_file and isinstance(media_file, bytes):
                mxc_uri, file_dict = await _upload_bytes(media_file)
                if not mxc_uri:
                    return self._adapter.make_error(
                        retcode=33001, message="媒体上传失败", raw=None
                    )
                content = self._build_media_content(
                    media_type,
                    mxc_uri,
                    media_filename or self._default_media_name(media_type),
                    file_dict=file_dict,
                )
            elif (
                media_file
                and isinstance(media_file, str)
                and media_file.startswith("mxc://")
            ):
                # 模块直传已有 mxc（无论明文/密文）不做二次处理
                content = self._build_media_content(
                    media_type, media_file, media_filename or full_text
                )
            elif (
                media_file
                and isinstance(media_file, str)
                and media_file.startswith(("http://", "https://"))
            ):
                result = await self._adapter._download_file(account_name, media_file)
                if not result:
                    return self._adapter.make_error(
                        retcode=33001, message="媒体下载失败", raw=None
                    )
                file_bytes, download_ct = result
                upload_ct = (
                    download_ct if download_ct and "text/" not in download_ct else None
                )
                mxc_uri, file_dict = await _upload_bytes(file_bytes, upload_ct)
                if not mxc_uri:
                    return self._adapter.make_error(
                        retcode=33001, message="媒体上传失败", raw=None
                    )
                mimetype = upload_ct
                url_name = os.path.basename(unquote(urlparse(media_file).path))
                content = self._build_media_content(
                    media_type,
                    mxc_uri,
                    url_name or media_filename or full_text,
                    mimetype=mimetype,
                    file_dict=file_dict,
                )
            elif media_file:
                resolve_error = ""
                resolved_bytes = None
                resolved_name = None
                value = str(media_file)
                if value.startswith("file://"):
                    value = url2pathname(urlparse(value).path)
                if value.startswith("data:"):
                    resolved_bytes = _decode_data_uri(value)
                    if resolved_bytes is None:
                        resolve_error = f"data: URI 解码失败({media_type})"
                else:
                    resolved_bytes = _decode_base64_media(value)
                path = Path(value)
                if resolved_bytes is None and path.is_file():
                    try:
                        resolved_bytes = path.read_bytes()
                        resolved_name = path.name
                    except OSError as e:
                        resolve_error = f"媒体文件读取失败({media_type}): {e}"
                elif resolved_bytes is None and not resolve_error:
                    resolve_error = f"媒体文件不存在({media_type}): {value}"
                if resolved_bytes is not None:
                    guessed_type, _ = mimetypes.guess_type(str(path))
                    mxc_uri, file_dict = await _upload_bytes(resolved_bytes, guessed_type)
                    if not mxc_uri:
                        return self._adapter.make_error(
                            retcode=33001, message="媒体上传失败", raw=None
                        )
                    content = self._build_media_content(
                        media_type,
                        mxc_uri,
                        media_filename or resolved_name or self._default_media_name(media_type),
                        mimetype=guessed_type,
                        file_dict=file_dict,
                    )

            skipped_note = ""
            if content is None and media_file is not None:
                self._adapter._get_logger().warning(
                    f"媒体段解析失败，已跳过: media_type={media_type}, file={str(media_file)[:200]}"
                )
                skipped_note = f"[跳过媒体段: {media_type}]"

            if content is None:
                has_text = bool("".join(text_parts).strip()) or bool(fallback_text) or bool(html_parts)
                if skipped_note and not has_text and location_data is None:
                    return self._adapter.make_error(
                        retcode=10002,
                        message=f"媒体段解析失败且无可发送内容: media_type={media_type}",
                        raw=None,
                    )
                if location_data is not None and not has_text:
                    # 纯位置消息：位置事件即主事件（m.location 无法与 m.text 合并进同一事件）
                    content = _build_location_content(location_data)
                    location_data = None
                else:
                    content = {
                        "msgtype": "m.notice" if is_notice else "m.text",
                        "body": full_text,
                    }
                    if full_html:
                        content["format"] = "org.matrix.custom.html"
                        content["formatted_body"] = full_html

            # 基类内置 Reply：_reply_message_id
            if self._reply_message_id:
                content["m.relates_to"] = {
                    "rel_type": "m.in_reply_to",
                    "event_id": self._reply_message_id,
                }

            all_mentioned = list(set(self._at_user_ids + segment_mentions))
            if all_mentioned or self._at_all:
                content["m.mentions"] = {}
                if all_mentioned:
                    content["m.mentions"]["user_ids"] = all_mentioned
                if self._at_all:
                    content["m.mentions"]["room"] = True

            # E2EE：加密房间经 nio 会话加密发送（自动分享 Megolm 会话并加密 content）
            if room_encrypted:
                ok, event_id, err = await session.send_event(
                    target_room, "m.room.message", content
                )
                if not ok:
                    self._adapter._get_logger().error(
                        i18n.t("matrix.e2ee_send_failed", name=account_name, error=err, default="账户 {name} 加密发送失败: {error}")
                    )
                    return self._adapter.make_error(retcode=34100, message=err, raw=None)
                resp = self._adapter.make_response(
                    status="ok",
                    retcode=0,
                    data={"event_id": event_id, "message_id": event_id},
                    message_id=event_id,
                    message="",
                    raw={"event_id": event_id},
                )
                if skipped_note:
                    resp["message"] = skipped_note
                if location_data is not None:
                    # 与文本/媒体共存的位置段：拆分为独立事件（规范 §6.3.3 顺序拆分）
                    ok_loc, _, err_loc = await session.send_event(
                        target_room, "m.room.message",
                        _build_location_content(location_data),
                    )
                    if not ok_loc:
                        resp["message"] = (
                            (resp.get("message") or "") + f" [位置事件发送失败: {err_loc}]"
                        ).strip()
                return resp

            txn_id = str(uuid.uuid4())
            endpoint = (
                f"/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_id}"
            )

            result = await self._adapter.call_api(
                endpoint=endpoint, method="PUT", _account_id=account_id, **content
            )
            if skipped_note:
                result["message"] = ((result.get("message") or "") + " " + skipped_note).strip()
            if location_data is not None:
                txn_loc = str(uuid.uuid4())
                loc_result = await self._adapter.call_api(
                    endpoint=f"/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_loc}",
                    method="PUT", _account_id=account_id,
                    **_build_location_content(location_data),
                )
                if loc_result.get("status") != "ok":
                    result["message"] = (
                        (result.get("message") or "")
                        + f" [位置事件发送失败: {loc_result.get('message', '')}]"
                    ).strip()
            return result

        @staticmethod
        def _default_media_name(media_type: str) -> str:
            """媒体类型的默认文件名（真实扩展名）"""
            return {
                "image": "image.png",
                "voice": "voice.ogg",
                "audio": "audio.mp3",
                "video": "video.mp4",
                "file": "file.bin",
            }.get(media_type, "file.bin")

        def _build_media_content(
            self, media_type: str, mxc_uri: str, body: str, mimetype: str = None,
            file_dict: Dict = None,
        ) -> Dict:
            msgtype_map = {
                "image": "m.image",
                "voice": "m.audio",
                "audio": "m.audio",
                "video": "m.video",
                "file": "m.file",
            }
            if not mimetype:
                mimetype_map = {
                    "image": "image/png",
                    "voice": "audio/ogg",
                    "audio": "audio/mpeg",
                    "video": "video/mp4",
                    "file": "application/octet-stream",
                }
                mimetype = mimetype_map.get(media_type, "application/octet-stream")
            media_content = {
                "msgtype": msgtype_map.get(media_type, "m.file"),
                "body": body or media_type,
                "info": {
                    "mimetype": mimetype,
                },
            }
            if file_dict:
                # 端到端加密媒体：m.file.encrypted（JWK）替代明文 url
                media_content["file"] = file_dict
            else:
                media_content["url"] = mxc_uri
            return media_content

    # ---- 适配器方法 ----
    def __init__(self, sdk_ref=None):
        super().__init__(sdk_ref)
        # 每个账户的运行时状态（登录后获得，不属于配置）
        # _account_runtime[name] = {bot_id, access_token, _next_batch, _dm_rooms, device_id}
        self._account_runtime: Dict[str, dict] = {}
        self._sync_tasks: Dict[str, asyncio.Task] = {}
        self._heartbeat_meta_tasks: Dict[str, asyncio.Task] = {}
        self._converters: Dict[str, MatrixConverter] = {}
        self._nio_sessions: Dict[str, NioSession] = {}
        self._running = False
        self._message_targets: Dict[str, str] = {}
        self._register_i18n()
        self._check_framework_version()
        self._get_logger().info(f"MatrixAdapter v{__version__} 已加载")

    @staticmethod
    def _parse_version(version_str: str) -> tuple:
        """解析版本号为可比较的三元组（忽略 dev/预发布后缀，如 2.8.0-dev.3 → (2, 8, 0)）"""
        parts = []
        for piece in str(version_str).split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _check_framework_version(self):
        """软依赖检测：框架版本过低时打警告（不阻断加载）"""
        try:
            from importlib.metadata import version as _pkg_version

            raw = _pkg_version("ErisPulse")
        except Exception:
            return
        try:
            if self._parse_version(raw) < MIN_FRAMEWORK_VERSION:
                self._get_logger().warning(
                    f"当前 ErisPulse 版本 {raw} 过低：MatrixAdapter v{__version__} 需要 >= "
                    f"{'.'.join(map(str, MIN_FRAMEWORK_VERSION))}"
                    "（BaseConverter / Api DSL / spawn_background 等特性），"
                    "部分功能可能不可用，建议升级框架"
                )
        except Exception:
            pass

    def _register_i18n(self):
        """注册配置字段与日志消息的 i18n 翻译"""
        try:
            from ErisPulse.runtime.config_schema import register_config_i18n
            register_config_i18n(MatrixAccountConfig, "zh-CN", domain="matrix")
            register_config_i18n(MatrixAccountConfig, "en", {
                "matrix.homeserver": "Matrix server address",
                "matrix.homeserver.ph": "https://matrix.org",
                "matrix.access_token": "Access Token (alternative to user_id/password)",
                "matrix.user_id": "Username (used for password login)",
                "matrix.password": "Password (used for password login)",
                "matrix.auto_accept_invites": "Automatically accept room invitations",
                "matrix.encryption_enabled": "Enable end-to-end encryption (auto encrypt/decrypt messages in encrypted rooms)",
                "matrix.trust_all_devices": "Automatically trust unverified devices (when disabled, message keys will not be shared with unverified devices)",
                "matrix.device_id": "Device ID (usually auto-detected; specify manually only if the server does not report it)",
                "matrix.store_path": "Crypto session store directory (default data/matrix/<account>, do not delete)",
                "matrix.store_path.ph": "data/matrix/<account>",
                "matrix.group.basic": "Basic",
                "matrix.group.connection": "Connection",
                "matrix.group.behavior": "Behavior",
                "matrix.group.encryption": "End-to-End Encryption",
            }, domain="matrix")
        except Exception:
            pass

        zh_CN = {
            "matrix.old_config_detected": "检测到旧格式单账户配置，建议迁移到 Matrix_Adapter.accounts.default",
            "matrix.save_default_failed": "保存默认配置失败: {error}",
            "matrix.missing_credentials": "账户 '{name}' 缺少 access_token/user_id，已跳过",
            "matrix.account_disabled": "账户 '{name}' 已加载但未启用（enabled=false），请在配置中将 [Matrix_Adapter.accounts.{name}] 的 enabled 设为 true",
            "matrix.accounts_loaded": "Matrix适配器初始化完成，共加载 {count} 个账户",
            "matrix.authenticated": "账户 {name} Matrix 已认证: {bot_id}",
            "matrix.login_success": "账户 {name} Matrix 登录成功: {bot_id}",
            "matrix.login_failed": "登录失败: {message}",
            "matrix.login_error": "账户 {name} Matrix 登录失败: {error}",
            "matrix.initial_sync_failed_nologin": "账户 {name} 初始同步失败且无法重新登录，停止同步",
            "matrix.initial_sync_still_failed": "账户 {name} 重新登录后初始同步仍失败，停止同步",
            "matrix.auth_failed": "账户 {name} 认证失败 ({count}/{max}): {message}",
            "matrix.relogin_success": "账户 {name} 重新登录成功，继续同步",
            "matrix.sync_failed": "账户 {name} 同步失败 ({count}/{max}): {message}",
            "matrix.max_failures_stop": "账户 {name} 连续 {max} 次失败，停止同步循环",
            "matrix.sync_loop_error": "账户 {name} 同步循环异常: {error}",
            "matrix.max_exceptions_stop": "账户 {name} 连续 {max} 次异常，停止同步循环",
            "matrix.relogin_attempt": "账户 {name} 尝试重新登录...",
            "matrix.relogin_done": "账户 {name} 重新登录成功",
            "matrix.relogin_failed": "账户 {name} 重新登录失败: {error}",
            "matrix.initial_sync_done": "账户 {name} 初始同步完成, next_batch: {batch}",
            "matrix.initial_sync_failed": "账户 {name} 初始同步失败: {message}",
            "matrix.dm_discovered": "账户 {name} 发现 {count} 个 DM 房间",
            "matrix.process_event": "账户 {name} 处理 Matrix 事件: {event}",
            "matrix.process_event_failed": "处理事件失败: {error}",
            "matrix.room_invite": "账户 {name} 收到房间邀请: {room}",
            "matrix.room_joined": "账户 {name} 已自动加入房间: {room}",
            "matrix.join_room_failed": "加入房间失败: {error}",
            "matrix.download_failed": "账户 {name} 下载文件失败: HTTP {status}",
            "matrix.download_error": "账户 {name} 下载文件异常: {error}",
            "matrix.account_not_found": "账户 {name} 不存在，无法上传媒体",
            "matrix.upload_failed": "账户 {name} 上传媒体失败: {data}",
            "matrix.upload_error": "账户 {name} 上传媒体异常: {error}",
            "matrix.api_timeout": "账户 {name} Matrix API 请求超时: {endpoint}",
            "matrix.timeout_msg": "请求超时",
            "matrix.api_call_failed": "账户 {name} 调用 Matrix API 失败: {error}",
            "matrix.api_error": "API调用失败: {error}",
            "matrix.no_enabled_accounts": "没有已启用的账户，Matrix 适配器将以空闲状态启动",
            "matrix.no_bot_id": "账户 {name} 无法获取 bot user_id，请检查配置",
            "matrix.account_started": "账户 {name} (bot_id: {bot_id}) Matrix 已启动",
            "matrix.adapter_started": "Matrix 适配器启动完成，共 {count} 个账户",
            "matrix.adapter_shutdown": "Matrix 适配器已关闭",
            "matrix.e2ee_nio_missing": "账户 {name} 已启用端到端加密，但 matrix-nio 未安装，将以明文模式运行。请执行: pip install matrix-nio[e2e]",
            "matrix.e2ee_device_id_missing": "账户 {name} 无法获取 device_id（服务器 /whoami 未返回且未配置），端到端加密已禁用。可在配置中手动指定 device_id",
            "matrix.e2ee_session_started": "账户 {name} 端到端加密会话已建立 (device: {device_id})",
            "matrix.e2ee_session_failed": "账户 {name} 端到端加密会话初始化失败，将以明文模式运行: {error}",
            "matrix.e2ee_relogin_rebuild": "账户 {name} 重新登录后正在重建端到端加密会话...",
            "matrix.e2ee_send_failed": "账户 {name} 加密发送失败: {error}",
            "matrix.e2ee_upload_failed": "账户 {name} 加密媒体上传失败: {error}",
            "matrix.e2ee_strict_mode": "账户 {name} 运行在严格信任模式：未验证设备将无法解密新消息",
        }
        en = {
            "matrix.old_config_detected": "Old single-account config detected, please migrate to Matrix_Adapter.accounts.default",
            "matrix.save_default_failed": "Failed to save default config: {error}",
            "matrix.missing_credentials": "Account '{name}' missing access_token/user_id, skipped",
            "matrix.account_disabled": "Account '{name}' loaded but disabled (enabled=false), please set enabled=true in [Matrix_Adapter.accounts.{name}]",
            "matrix.accounts_loaded": "Matrix adapter initialized, loaded {count} account(s)",
            "matrix.authenticated": "Account {name} Matrix authenticated: {bot_id}",
            "matrix.login_success": "Account {name} Matrix login success: {bot_id}",
            "matrix.login_failed": "Login failed: {message}",
            "matrix.login_error": "Account {name} Matrix login failed: {error}",
            "matrix.initial_sync_failed_nologin": "Account {name} initial sync failed and re-login not possible, stopping sync",
            "matrix.initial_sync_still_failed": "Account {name} initial sync still failing after re-login, stopping sync",
            "matrix.auth_failed": "Account {name} auth failed ({count}/{max}): {message}",
            "matrix.relogin_success": "Account {name} re-login success, resuming sync",
            "matrix.sync_failed": "Account {name} sync failed ({count}/{max}): {message}",
            "matrix.max_failures_stop": "Account {name} failed {max} consecutive times, stopping sync loop",
            "matrix.sync_loop_error": "Account {name} sync loop error: {error}",
            "matrix.max_exceptions_stop": "Account {name} {max} consecutive exceptions, stopping sync loop",
            "matrix.relogin_attempt": "Account {name} attempting re-login...",
            "matrix.relogin_done": "Account {name} re-login success",
            "matrix.relogin_failed": "Account {name} re-login failed: {error}",
            "matrix.initial_sync_done": "Account {name} initial sync done, next_batch: {batch}",
            "matrix.initial_sync_failed": "Account {name} initial sync failed: {message}",
            "matrix.dm_discovered": "Account {name} discovered {count} DM room(s)",
            "matrix.process_event": "Account {name} processing Matrix event: {event}",
            "matrix.process_event_failed": "Failed to process event: {error}",
            "matrix.room_invite": "Account {name} received room invite: {room}",
            "matrix.room_joined": "Account {name} auto-joined room: {room}",
            "matrix.join_room_failed": "Failed to join room: {error}",
            "matrix.download_failed": "Account {name} download failed: HTTP {status}",
            "matrix.download_error": "Account {name} download error: {error}",
            "matrix.account_not_found": "Account {name} not found, cannot upload media",
            "matrix.upload_failed": "Account {name} upload media failed: {data}",
            "matrix.upload_error": "Account {name} upload media error: {error}",
            "matrix.api_timeout": "Account {name} Matrix API request timeout: {endpoint}",
            "matrix.timeout_msg": "Request timeout",
            "matrix.api_call_failed": "Account {name} Matrix API call failed: {error}",
            "matrix.api_error": "API call failed: {error}",
            "matrix.no_enabled_accounts": "No enabled accounts, Matrix adapter will start idle",
            "matrix.no_bot_id": "Account {name} could not obtain bot user_id, please check config",
            "matrix.account_started": "Account {name} (bot_id: {bot_id}) Matrix started",
            "matrix.adapter_started": "Matrix adapter started, {count} account(s) total",
            "matrix.adapter_shutdown": "Matrix adapter shut down",
            "matrix.e2ee_nio_missing": "Account {name} has encryption enabled but matrix-nio is not installed, running in plaintext mode. Run: pip install matrix-nio[e2e]",
            "matrix.e2ee_device_id_missing": "Account {name} could not obtain device_id (server /whoami did not return one and none configured), end-to-end encryption disabled. You may specify device_id in config manually",
            "matrix.e2ee_session_started": "Account {name} end-to-end encryption session established (device: {device_id})",
            "matrix.e2ee_session_failed": "Account {name} failed to initialize E2EE session, running in plaintext mode: {error}",
            "matrix.e2ee_relogin_rebuild": "Account {name} rebuilding E2EE session after re-login...",
            "matrix.e2ee_send_failed": "Account {name} encrypted send failed: {error}",
            "matrix.e2ee_upload_failed": "Account {name} encrypted media upload failed: {error}",
            "matrix.e2ee_strict_mode": "Account {name} running in strict trust mode: unverified devices will not be able to decrypt new messages",
        }
        try:
            i18n.register("zh-CN", zh_CN, domain="matrix")
            i18n.register("en", en, domain="matrix")
        except Exception:
            pass

    def _get_config_key(self) -> str:
        return "Matrix_Adapter"

    async def _delete_message_by_id(self, message_id: str, account_id: Optional[str] = None) -> dict:
        """按消息登记表撤回消息（Matrix redact 需要 room_id + event_id）"""
        room_id = self._message_targets.get(message_id)
        if not room_id:
            return self.make_error(retcode=10003, message=f"未找到消息 {message_id} 的目标上下文，无法撤回")
        import uuid as _uuid

        txn_id = _uuid.uuid4().hex
        return await self.call_api(
            f"/_matrix/client/v3/rooms/{room_id}/redact/{message_id}/{txn_id}",
            method="PUT", _account_id=account_id,
        )

    def _migrate_legacy_config(self):
        """将旧版单账户扁平配置（access_token 直接在 [Matrix_Adapter] 下）迁移到 accounts 结构"""
        try:
            from ErisPulse.Core import config as config_mgr

            key = self._get_config_key()
            data = config_mgr.getConfig(key)
            if not isinstance(data, dict):
                return
            accounts = data.get("accounts")
            if isinstance(accounts, dict) and accounts:
                return
            if not data.get("access_token"):
                return
            data["accounts"] = {"default": {**data, "enabled": True}}
            config_mgr.setConfig(key, data)
            self.logger.info("已将旧版单账户配置迁移到 accounts.default")
        except Exception as e:
            self.logger.debug(f"旧配置迁移检查跳过: {e}")

    def _get_runtime(self, name: str) -> dict:
        if name not in self._account_runtime:
            self._account_runtime[name] = {
                "bot_id": "",
                "access_token": "",
                "_next_batch": None,
                "_dm_rooms": {},
                "device_id": "",
            }
        return self._account_runtime[name]

    def _resolve_target_room(self, target: str, account_name: str) -> tuple:
        """解析标准发送目标为 Matrix 房间 ID

        会话类型映射（session-types 标准）：Send.To("group", x) 的 x 是房间 ID，
        Send.To("user", x) 的 x 是用户 ID——Matrix 中私聊实际仍发到房间：
        - "!room:server" → 房间 ID，直接使用
        - "@user:server" → 反查 DM 房间表（_dm_rooms: room_id → user_id）得到私聊房间
        - 其余形态无法定位 Matrix 房间

        :return: (room_id 或 None, 错误描述或空串)
        """
        target = str(target or "").strip()
        if not target:
            return None, "发送目标为空"
        if target.startswith("!"):
            return target, ""
        if target.startswith("@"):
            dm_rooms = self._get_runtime(account_name).get("_dm_rooms", {})
            for room_id, user_id in dm_rooms.items():
                if user_id == target:
                    return room_id, ""
            return None, (
                f"未找到与 {target} 的私聊房间；请改用房间 ID（! 开头）发送，"
                "或先由对方发起私聊以建立 DM 会话"
            )
        return None, f"无法识别的发送目标: {target}（需为 ! 开头的房间 ID 或 @ 开头的用户 ID）"

    # ==================== 端到端加密（E2EE） ====================

    def _e2ee_store_path(self, account_name: str, account: MatrixAccountConfig) -> str:
        if account.store_path:
            return account.store_path
        return str(Path("data") / "matrix" / account_name)

    def _load_persisted_device_id(self, account_name: str, account: MatrixAccountConfig) -> str:
        try:
            device_file = Path(self._e2ee_store_path(account_name, account)) / "device.json"
            if device_file.is_file():
                import json as _json

                return (_json.loads(device_file.read_text("utf-8")) or {}).get("device_id", "")
        except Exception:
            pass
        return ""

    def _persist_device_id(self, account_name: str, account: MatrixAccountConfig, device_id: str):
        try:
            import json as _json

            store_dir = Path(self._e2ee_store_path(account_name, account))
            store_dir.mkdir(parents=True, exist_ok=True)
            (store_dir / "device.json").write_text(
                _json.dumps({"device_id": device_id}), encoding="utf-8"
            )
        except Exception as e:
            self._get_logger().debug(f"持久化 device_id 失败: {e}")

    def _resolve_device_id(self, account_name: str, account: MatrixAccountConfig) -> str:
        """device_id 解析优先级：配置项 > 持久化值 > 登录响应（runtime）"""
        if account.device_id:
            return account.device_id
        persisted = self._load_persisted_device_id(account_name, account)
        if persisted:
            return persisted
        return self._get_runtime(account_name).get("device_id", "")

    def _nio_session_for(self, account_name: str) -> Optional[NioSession]:
        """返回账户的活跃 E2EE 会话；未启用/未就绪返回 None（发送侧据此走明文 REST）"""
        session = self._nio_sessions.get(account_name)
        if session is not None and session.started:
            return session
        return None

    @staticmethod
    def decrypt_media(file_bytes: bytes, key: str, iv: str, hash_value: str) -> bytes:
        """解密端到端加密媒体

        配合事件媒体段中的 matrix_encrypted_file 字段（JWK）使用：
        先按其中 url 下载密文，再用本方法解密。
        """
        if not NIO_AVAILABLE:
            raise RuntimeError("matrix-nio is not installed")
        from nio.crypto import decrypt_attachment

        return decrypt_attachment(file_bytes, key, hash_value, iv)

    async def _init_e2ee_session(self, account_name: str, account: MatrixAccountConfig):
        """登录成功后初始化 E2EE 会话；不可用时降级明文并提示"""
        logger = self._get_logger()
        if not account.encryption_enabled:
            return
        if not NIO_AVAILABLE:
            logger.warning(
                i18n.t("matrix.e2ee_nio_missing", name=account_name, default="账户 {name} 已启用端到端加密，但 matrix-nio 未安装，将以明文模式运行。请执行: pip install matrix-nio[e2e]")
            )
            return

        runtime = self._get_runtime(account_name)
        device_id = self._resolve_device_id(account_name, account)
        if not device_id:
            logger.warning(
                i18n.t("matrix.e2ee_device_id_missing", name=account_name, default="账户 {name} 无法获取 device_id（服务器 /whoami 未返回且未配置），端到端加密已禁用。可在配置中手动指定 device_id")
            )
            return
        runtime["device_id"] = device_id
        self._persist_device_id(account_name, account, device_id)

        session = self._nio_sessions.get(account_name)
        if session is not None and session.started:
            return

        session = NioSession(
            account_name=account_name,
            homeserver=account.homeserver or "https://matrix.org",
            user_id=runtime.get("bot_id", ""),
            device_id=device_id,
            store_path=self._e2ee_store_path(account_name, account),
            trust_all_devices=account.trust_all_devices,
            logger=logger,
        )
        if await session.start(runtime.get("access_token", "")):
            self._nio_sessions[account_name] = session
            logger.info(
                i18n.t("matrix.e2ee_session_started", name=account_name, device_id=device_id, default="账户 {name} 端到端加密会话已建立 (device: {device_id})")
            )
            if not account.trust_all_devices:
                logger.info(
                    i18n.t("matrix.e2ee_strict_mode", name=account_name, default="账户 {name} 运行在严格信任模式：未验证设备将无法解密新消息")
                )
        else:
            logger.error(
                i18n.t("matrix.e2ee_session_failed", name=account_name, error="start() returned False", default="账户 {name} 端到端加密会话初始化失败，将以明文模式运行: {error}")
            )

    async def _teardown_e2ee_session(self, account_name: str):
        session = self._nio_sessions.pop(account_name, None)
        if session is not None:
            await session.close()

    # 账户加载使用框架默认实现（AccountConfigClass 模板 + 校验）；
    # 旧版单账户扁平配置迁移见 _migrate_legacy_config；缺少凭据的账户由 _login_if_needed 失败自然跳过。

    async def _login_if_needed(self, account_name: str, account: MatrixAccountConfig):
        runtime = self._get_runtime(account_name)
        converter = self._converters.get(account_name)

        if account.access_token:
            try:
                result = await self.call_api(
                    endpoint="/_matrix/client/v3/account/whoami",
                    method="GET",
                    _account_id=account_name,
                )
                if result.get("status") == "ok" and result.get("data"):
                    bot_id = result["data"].get("user_id", "")
                    runtime["bot_id"] = bot_id
                    runtime["access_token"] = account.access_token
                    # Matrix v1.11+ 的 /whoami 会返回 device_id（E2EE 需要）
                    if result["data"].get("device_id"):
                        runtime["device_id"] = str(result["data"]["device_id"])
                    if converter:
                        converter.bot_user_id = bot_id
                    self.logger.info(i18n.t("matrix.authenticated", name=account_name, bot_id=bot_id, default="账户 {name} Matrix 已认证: {bot_id}"))
                    return
            except Exception:
                pass

        if account.user_id and account.password:
            try:
                login_data = {
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": account.user_id},
                    "password": account.password,
                }
                # 复用已持久化的 device_id，避免每次重登产生新设备（E2EE 会话绑定设备）
                existing_device_id = self._resolve_device_id(account_name, account)
                if existing_device_id:
                    login_data["device_id"] = existing_device_id
                result = await self.call_api(
                    endpoint="/_matrix/client/v3/login",
                    method="POST",
                    _account_id=account_name,
                    **login_data,
                )
                if result.get("status") == "ok" and result.get("data"):
                    token = result["data"].get("access_token", "")
                    bot_id = result["data"].get("user_id", account.user_id)
                    # 密码登录获得的 token 写入运行时状态，供后续 call_api 使用
                    runtime["access_token"] = token
                    runtime["bot_id"] = bot_id
                    if result["data"].get("device_id"):
                        runtime["device_id"] = str(result["data"]["device_id"])
                    if converter:
                        converter.bot_user_id = bot_id
                    self.logger.info(i18n.t("matrix.login_success", name=account_name, bot_id=bot_id, default="账户 {name} Matrix 登录成功: {bot_id}"))
                else:
                    raise Exception(
                        i18n.t("matrix.login_failed", message=result.get('message', 'Unknown error'), default="登录失败: {message}")
                    )
            except Exception as e:
                self.logger.error(i18n.t("matrix.login_error", name=account_name, error=e, default="账户 {name} Matrix 登录失败: {error}"))
                raise

    async def _do_sync_request(self, account_name: str, timeout_ms: int = 30000) -> dict:
        """执行一次同步请求，返回与 call_api 一致的结构

        E2EE 会话活跃时走 nio（自动加解密与密钥管理），否则走 REST 长轮询。
        """
        session = self._nio_session_for(account_name)
        if session is not None:
            try:
                data = await session.sync(timeout_ms)
            except NioSessionError as e:
                return {"status": "failed", "message": str(e), "http_status": e.status_code}
            except Exception as e:  # noqa: BLE001
                return {"status": "failed", "message": f"{type(e).__name__}: {e}", "http_status": 0}
            if data is None:
                return {"status": "failed", "message": "nio sync returned no data", "http_status": 0}
            return {"status": "ok", "data": data}

        runtime = self._get_runtime(account_name)
        return await self.call_api(
            endpoint=f"/_matrix/client/v3/sync?since={runtime.get('_next_batch') or ''}&timeout={timeout_ms}",
            method="GET",
            _account_id=account_name,
        )

    async def _sync_loop(self, account_name: str):
        account = self.accounts.get(account_name)
        max_failures = 5

        # 初始同步——如果认证失败，尝试重新登录后重试
        if not await self._initial_sync(account_name):
            if not await self._try_relogin(account_name, account):
                self.logger.error(
                    i18n.t("matrix.initial_sync_failed_nologin", name=account_name, default="账户 {name} 初始同步失败且无法重新登录，停止同步")
                )
                await self._on_sync_loop_exit(account_name)
                return
            if not await self._initial_sync(account_name):
                self.logger.error(
                    i18n.t("matrix.initial_sync_still_failed", name=account_name, default="账户 {name} 重新登录后初始同步仍失败，停止同步")
                )
                await self._on_sync_loop_exit(account_name)
                return

        await self._discover_dm_rooms(account_name)

        consecutive_failures = 0

        while self._running:
            try:
                runtime = self._get_runtime(account_name)
                result = await self._do_sync_request(account_name)

                if result.get("status") != "ok":
                    message = result.get("message", "")
                    http_status = result.get("http_status", 0)
                    is_auth_error = (
                        http_status in (401, 403)
                        or "authorization" in message.lower()
                        or "token" in message.lower()
                    )

                    if is_auth_error:
                        consecutive_failures += 1
                        self.logger.warning(
                            i18n.t("matrix.auth_failed", name=account_name, count=consecutive_failures, max=max_failures, message=message, default="账户 {name} 认证失败 ({count}/{max}): {message}")
                        )
                        if await self._try_relogin(account_name, account):
                            self.logger.info(
                                i18n.t("matrix.relogin_success", name=account_name, default="账户 {name} 重新登录成功，继续同步")
                            )
                            consecutive_failures = 0
                            continue
                    else:
                        consecutive_failures += 1
                        self.logger.error(
                            i18n.t("matrix.sync_failed", name=account_name, count=consecutive_failures, max=max_failures, message=message, default="账户 {name} 同步失败 ({count}/{max}): {message}")
                        )

                    if consecutive_failures >= max_failures:
                        self.logger.error(
                            i18n.t("matrix.max_failures_stop", name=account_name, max=max_failures, default="账户 {name} 连续 {max} 次失败，停止同步循环")
                        )
                        break

                    # 指数退避: 5, 10, 20, 40, 60
                    backoff = min(5 * (2 ** (consecutive_failures - 1)), 60)
                    await asyncio.sleep(backoff)
                    continue

                # 成功——重置计数器
                consecutive_failures = 0
                data = result.get("data", {})
                runtime["_next_batch"] = data.get(
                    "next_batch", runtime.get("_next_batch")
                )

                await self._process_sync_response(account_name, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(i18n.t("matrix.sync_loop_error", name=account_name, error=e, default="账户 {name} 同步循环异常: {error}"))
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    self.logger.error(
                        i18n.t("matrix.max_exceptions_stop", name=account_name, max=max_failures, default="账户 {name} 连续 {max} 次异常，停止同步循环")
                    )
                    break
                await asyncio.sleep(min(5 * consecutive_failures, 30))

        await self._on_sync_loop_exit(account_name)

    async def _try_relogin(self, account_name: str, account) -> bool:
        """尝试重新登录。成功返回 True，失败或无凭据返回 False。"""
        if not account or not account.user_id or not account.password:
            return False
        try:
            self.logger.info(i18n.t("matrix.relogin_attempt", name=account_name, default="账户 {name} 尝试重新登录..."))
            # 清空旧 token，强制走密码登录路径
            old_token = account.access_token
            account.access_token = ""
            try:
                await self._login_if_needed(account_name, account)
            except Exception:
                # 恢复旧 token，避免状态污染
                account.access_token = old_token
                raise
            self.logger.info(i18n.t("matrix.relogin_done", name=account_name, default="账户 {name} 重新登录成功"))
            # access_token 已更换，用新 token 重建 E2EE 会话（store / device_id 复用）
            account_cfg = self.accounts.get(account_name)
            if account_cfg is not None and getattr(account_cfg, "encryption_enabled", False):
                self.logger.info(
                    i18n.t("matrix.e2ee_relogin_rebuild", name=account_name, default="账户 {name} 重新登录后正在重建端到端加密会话...")
                )
                await self._teardown_e2ee_session(account_name)
                await self._init_e2ee_session(account_name, account_cfg)
            return True
        except Exception as e:
            self.logger.error(i18n.t("matrix.relogin_failed", name=account_name, error=e, default="账户 {name} 重新登录失败: {error}"))
            return False

    async def _on_sync_loop_exit(self, account_name: str):
        """同步循环因错误退出时清理状态"""
        if not self._running:
            return
        runtime = self._get_runtime(account_name)
        bot_id = runtime.get("bot_id", "")
        if bot_id:
            try:
                await self.emit_meta("disconnect", bot_id)
            except Exception:
                pass
        # 停止该账户的心跳任务
        task = self._heartbeat_meta_tasks.pop(account_name, None)
        if task and not task.done():
            task.cancel()

    async def _initial_sync(self, account_name: str) -> bool:
        runtime = self._get_runtime(account_name)
        result = await self._do_sync_request(account_name, timeout_ms=0)
        if result.get("status") == "ok" and result.get("data"):
            data = result["data"]
            # E2EE 模式下 next_batch 由 nio 维护，这里同步到 runtime 仅作展示
            session = self._nio_session_for(account_name)
            next_batch = session.next_batch if session else data.get("next_batch", "")
            runtime["_next_batch"] = next_batch or data.get("next_batch", "")
            self.logger.info(
                i18n.t("matrix.initial_sync_done", name=account_name, batch=runtime.get('_next_batch'), default="账户 {name} 初始同步完成, next_batch: {batch}")
            )
            return True
        else:
            self.logger.warning(
                i18n.t("matrix.initial_sync_failed", name=account_name, message=result.get('message', 'Unknown error'), default="账户 {name} 初始同步失败: {message}")
            )
            return False

    async def _discover_dm_rooms(self, account_name: str):
        runtime = self._get_runtime(account_name)
        bot_id = runtime.get("bot_id", "")
        converter = self._converters.get(account_name)
        result = await self.call_api(
            endpoint=f"/_matrix/client/v3/user/{bot_id}/account_data/m.direct",
            method="GET",
            _account_id=account_name,
        )
        if result.get("status") == "ok" and result.get("data"):
            dm_data = result["data"]
            dm_rooms: Dict[str, str] = {}
            for user_id, room_ids in dm_data.items():
                if isinstance(room_ids, list) and room_ids:
                    dm_rooms[room_ids[0]] = user_id
            runtime["_dm_rooms"] = dm_rooms
            if converter:
                converter.set_dm_rooms(dm_rooms)
            self.logger.info(i18n.t("matrix.dm_discovered", name=account_name, count=len(dm_rooms), default="账户 {name} 发现 {count} 个 DM 房间"))

    async def _process_sync_response(self, account_name: str, data: dict):
        runtime = self._get_runtime(account_name)
        bot_id = runtime.get("bot_id", "")
        dm_rooms = runtime.get("_dm_rooms", {})
        converter = self._converters.get(account_name)
        joined_rooms = data.get("rooms", {}).get("join", {})
        for room_id, room_data in joined_rooms.items():
            is_dm = room_id in dm_rooms

            timeline = room_data.get("timeline", {})
            events = timeline.get("events", [])

            for event in events:
                self.logger.debug(i18n.t("matrix.process_event", name=account_name, event=event, default="账户 {name} 处理 Matrix 事件: {event}"))
                try:
                    if converter:
                        onebot_event = converter.convert(
                            event, room_id=room_id, is_dm=is_dm
                        )
                    else:
                        onebot_event = None
                    if onebot_event:
                        if onebot_event.get("type") == "message" and onebot_event.get("message_id"):
                            self._message_targets[str(onebot_event["message_id"])] = str(room_id)
                            while len(self._message_targets) > 800:
                                self._message_targets.pop(next(iter(self._message_targets)), None)
                        onebot_event.setdefault("self", {})["account_id"] = account_name
                        await self.sdk.adapter.emit(onebot_event)
                except Exception as e:
                    self.logger.error(i18n.t("matrix.process_event_failed", error=e, default="处理事件失败: {error}"))

        invite_rooms = data.get("rooms", {}).get("invite", {})
        for room_id, room_data in invite_rooms.items():
            invite_state = room_data.get("invite_state", {}).get("events", [])
            for event in invite_state:
                if event.get("type") == "m.room.member":
                    content = event.get("content", {})
                    if (
                        content.get("membership") == "invite"
                        and event.get("state_key") == bot_id
                    ):
                        self.logger.info(i18n.t("matrix.room_invite", name=account_name, room=room_id, default="账户 {name} 收到房间邀请: {room}"))
                        if getattr(
                            self.accounts.get(account_name), "auto_accept_invites", True
                        ):
                            try:
                                await self.call_api(
                                    endpoint=f"/_matrix/client/v3/join/{room_id}",
                                    method="POST",
                                    _account_id=account_name,
                                )
                                self.logger.info(
                                    i18n.t("matrix.room_joined", name=account_name, room=room_id, default="账户 {name} 已自动加入房间: {room}")
                                )
                            except Exception as e:
                                self.logger.error(i18n.t("matrix.join_room_failed", error=e, default="加入房间失败: {error}"))

    async def _download_file(self, account_name: str, url: str) -> Optional[tuple]:
        try:
            resp = await client.get(url, timeout=300)
            if resp.status != 200:
                self.logger.error(
                    i18n.t("matrix.download_failed", name=account_name, status=resp.status, default="账户 {name} 下载文件失败: HTTP {status}")
                )
                return None
            content_type = (
                (resp.headers.get("Content-Type", "") or "").split(";")[0].strip()
            )
            data = await resp.read()
            return (data, content_type)
        except Exception as e:
            self.logger.error(i18n.t("matrix.download_error", name=account_name, error=e, default="账户 {name} 下载文件异常: {error}"))
            return None

    async def _upload_media(
        self,
        account_name: str,
        file: bytes,
        media_type: str,
        content_type: str = None,
    ) -> Optional[str]:
        account = self.accounts.get(account_name)
        if not account:
            self.logger.error(i18n.t("matrix.account_not_found", name=account_name, default="账户 {name} 不存在，无法上传媒体"))
            return None
        if not content_type:
            content_type_map = {
                "image": "image/png",
                "voice": "audio/ogg",
                "audio": "audio/mpeg",
                "video": "video/mp4",
                "file": "application/octet-stream",
            }
            content_type = content_type_map.get(media_type, "application/octet-stream")

        homeserver = (account.homeserver or "https://matrix.org").rstrip("/")
        url = f"{homeserver}/_matrix/media/v3/upload"
        # 优先使用运行时 token（同 call_api）
        runtime = self._get_runtime(account_name)
        access_token = runtime.get("access_token") or account.access_token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
        }

        try:
            resp = await client.post(url, data=file, headers=headers, timeout=300)
            data = await resp.json()
            if resp.status == 200:
                return data.get("content_uri", "")
            else:
                self.logger.error(i18n.t("matrix.upload_failed", name=account_name, data=data, default="账户 {name} 上传媒体失败: {data}"))
                return None
        except Exception as e:
            self.logger.error(i18n.t("matrix.upload_error", name=account_name, error=e, default="账户 {name} 上传媒体异常: {error}"))
            return None

    async def call_api(
        self, endpoint: str, method: str = "POST", _account_id: str = None, **params
    ):
                # 标准字段 account_id 与旧名 _account_id 双兼容（ApiDSL.Using 传标准名）
        account_id = params.pop("account_id", None) or _account_id
        account_name, account = self._resolve_account(account_id)
        homeserver = (account.homeserver or "https://matrix.org").rstrip("/")
        url = f"{homeserver}{endpoint}"
        # 优先使用运行时 token（密码登录后获得），避免 self.accounts 每次重新读取配置导致 token 丢失
        runtime = self._get_runtime(account_name)
        access_token = runtime.get("access_token") or account.access_token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        echo = params.pop("echo", None)

        try:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            elif method.upper() == "PUT":
                resp = await client.put(url, json=params, headers=headers)
            else:
                resp = await client.post(url, json=params, headers=headers)

            try:
                raw_response = await resp.json()
            except Exception:
                raw_response = {}

            success = 200 <= resp.status < 300

            if not isinstance(raw_response, dict):
                response = self.make_response(
                    status="ok" if success else "failed",
                    retcode=0 if success else 34100,
                    data=raw_response,
                    message_id="",
                    message="",
                    raw=raw_response,
                )
                response["matrix_raw"] = raw_response
                response["http_status"] = resp.status
                if echo:
                    response["echo"] = echo
                return response

            event_id = raw_response.get("event_id", "")
            message_id = str(event_id) if event_id else ""
            data = dict(raw_response)
            data["message_id"] = message_id

            # retcode 必须为 int（api-response §2）；Matrix 的 errcode 是字符串
            # （如 "M_FORBIDDEN"），恒降级为 34xxx 平台错误段，原始 errcode 保留在
            # message 与 matrix_raw 中
            errcode = raw_response.get("errcode", "")
            response = self.make_response(
                status="ok" if success else "failed",
                retcode=0 if success else 34100,
                data=data,
                message_id=message_id,
                message=""
                if success
                else (
                    raw_response.get("error")
                    or (f"{errcode} (HTTP {resp.status})" if errcode else f"HTTP {resp.status}")
                ),
                raw=raw_response,
            )
            response["matrix_raw"] = raw_response
            response["http_status"] = resp.status
            if echo:
                response["echo"] = echo
            return response

        except asyncio.TimeoutError:
            self.logger.error(i18n.t("matrix.api_timeout", name=account_name, endpoint=endpoint, default="账户 {name} Matrix API 请求超时: {endpoint}"))
            err = self.make_error(retcode=33001, message=i18n.t("matrix.timeout_msg", default="请求超时"), raw=None)
            if echo:
                err["echo"] = echo
            return err
        except Exception as e:
            self.logger.error(i18n.t("matrix.api_call_failed", name=account_name, error=e, default="账户 {name} 调用 Matrix API 失败: {error}"))
            err = self.make_error(
                retcode=33000, message=i18n.t("matrix.api_error", error=str(e), default="API调用失败: {error}"), raw=None
            )
            if echo:
                err["echo"] = echo
            return err

    async def _heartbeat_meta_loop(self, account_name: str):
        try:
            while self._running:
                await asyncio.sleep(30)
                runtime = self._get_runtime(account_name)
                await self.emit_meta("heartbeat", runtime.get("bot_id", ""))
        except asyncio.CancelledError:
            pass

    # ==================== Api DSL ====================

    class Api(BaseAdapter.Api):
        """Matrix 标准 API 动作实现（ApiDSL，基于 Matrix Client-Server API）

        {!--< tips >!--}
        1. get_self_info → GET /account/whoami + GET /profile/{self}
        2. get_user_info → GET /profile/{userId}
        3. get_group_info → GET /rooms/{roomId}/state/m.room.name；set_group_name → PUT 同端点
        4. get_group_list → GET /joined_rooms；get_group_member_list → GET /rooms/{roomId}/members
        5. get_group_member_info → GET /rooms/{roomId}/state/m.room.member/{userId}
        6. delete_message → PUT /rooms/{roomId}/redact/{eventId}/{txn}（登记表补全 roomId）
        7. leave_group → POST /rooms/{roomId}/leave；get_friend_list 不支持（10002，Matrix 无好友概念）
        {!--< /tips >!--}
        """

        async def get_self_info(self) -> dict:
            r = await self._adapter.call_api(
                "/_matrix/client/v3/account/whoami", method="GET",
                _account_id=self._account_id,
            )
            if r.get("status") != "ok":
                return r
            u = r.get("data") or {}
            user_id = str(u.get("user_id", ""))
            local_name = user_id.split(":")[0].lstrip("@")
            # whoami 不含资料，补 GET /profile 取 displayname/avatar
            displayname, avatar_url = "", ""
            if user_id:
                p = await self._adapter.call_api(
                    f"/_matrix/client/v3/profile/{user_id}", method="GET",
                    _account_id=self._account_id,
                )
                if p.get("status") == "ok" and isinstance(p.get("data"), dict):
                    displayname = str(p["data"].get("displayname", "") or "")
                    avatar_url = str(p["data"].get("avatar_url", "") or "")
            r["data"] = {
                "user_id": user_id,
                "user_name": local_name,
                "user_displayname": displayname or local_name,
                "user_avatar": avatar_url,
            }
            return r

        async def get_user_info(self, user_id: str) -> dict:
            r = await self._adapter.call_api(
                f"/_matrix/client/v3/profile/{user_id}", method="GET",
                _account_id=self._account_id,
            )
            if r.get("status") != "ok":
                return r
            u = r.get("data") or {}
            displayname = str(u.get("displayname", "") or "")
            r["data"] = {
                "user_id": str(user_id),
                "user_name": displayname,
                "user_displayname": displayname,
                "user_remark": "",
                "user_avatar": u.get("avatar_url", ""),
            }
            return r

        async def get_friend_list(self) -> dict:
            # Matrix 协议无好友概念（关系即房间共享成员），明确声明不支持
            return self._adapter.make_error(
                retcode=10002, message="Matrix 平台无好友概念，不支持 get_friend_list", raw=None
            )

        async def get_group_info(self, group_id: str) -> dict:
            r = await self._adapter.call_api(
                f"/_matrix/client/v3/rooms/{group_id}/state/m.room.name", method="GET",
                _account_id=self._account_id,
            )
            if r.get("status") != "ok":
                return r
            r["data"] = {"group_id": str(group_id), "group_name": (r.get("data") or {}).get("name", "")}
            return r

        async def get_group_list(self) -> dict:
            r = await self._adapter.call_api(
                "/_matrix/client/v3/joined_rooms", method="GET", _account_id=self._account_id
            )
            if r.get("status") != "ok":
                return r
            rooms = (r.get("data") or {}).get("joined_rooms", []) or []
            r["data"] = [{"group_id": str(x), "group_name": ""} for x in rooms]
            return r

        async def get_group_member_info(self, group_id: str, user_id: str) -> dict:
            r = await self._adapter.call_api(
                f"/_matrix/client/v3/rooms/{group_id}/state/m.room.member/{user_id}",
                method="GET", _account_id=self._account_id,
            )
            if r.get("status") != "ok":
                return r
            u = r.get("data") or {}
            displayname = str(u.get("displayname", "") or "")
            r["data"] = {
                "user_id": str(user_id),
                "user_name": displayname or str(user_id).split(":")[0].lstrip("@"),
                "user_displayname": displayname,
            }
            return r

        async def get_group_member_list(self, group_id: str) -> dict:
            r = await self._adapter.call_api(
                f"/_matrix/client/v3/rooms/{group_id}/members",
                method="GET", _account_id=self._account_id,
            )
            if r.get("status") != "ok":
                return r
            chunk = (r.get("data") or {}).get("chunk", []) or []
            members = []
            for m in chunk:
                if not (isinstance(m, dict) and m.get("sender")):
                    continue
                m_content = m.get("content") or {}
                # OB12 语义为当前在群成员：过滤 leave/ban（invite 视为待加入，保留）
                if m_content.get("membership") not in (None, "join", "invite"):
                    continue
                sender = str(m["sender"])
                displayname = str(m_content.get("displayname", "") or "")
                members.append({
                    "user_id": sender,
                    "user_name": displayname or sender.split(":")[0].lstrip("@"),
                    "user_displayname": displayname,
                })
            r["data"] = members
            return r

        async def set_group_name(self, group_id: str, group_name: str) -> dict:
            return await self._adapter.call_api(
                f"/_matrix/client/v3/rooms/{group_id}/state/m.room.name",
                method="PUT", _account_id=self._account_id,
                name=str(group_name),
            )

        async def leave_group(self, group_id: str) -> dict:
            return await self._adapter.call_api(
                f"/_matrix/client/v3/rooms/{group_id}/leave", method="POST",
                _account_id=self._account_id,
            )

        async def delete_message(self, message_id: str) -> dict:
            return await self._adapter._delete_message_by_id(str(message_id), account_id=self._account_id)

        async def get_status(self) -> dict:
            ad = self._adapter
            bots = []
            for name, runtime in ad._account_runtime.items():
                bots.append({
                    "self": {"platform": ad.platform, "user_id": runtime.get("bot_id", ""), "account_id": name},
                    "online": bool(runtime.get("bot_id")),
                })
            return ad.make_response(data={"good": any(b["online"] for b in bots), "bots": bots})

        async def get_version(self) -> dict:
            from . import __version__

            return self._adapter.make_response(
                data={"impl": "ErisPulse-MatrixAdapter", "version": __version__, "onebot_version": "12"}
            )

        async def get_supported_actions(self) -> dict:
            actions = {
                "get_self_info", "get_user_info", "get_group_info", "get_group_list",
                "get_group_member_info", "get_group_member_list", "set_group_name",
                "leave_group", "delete_message",
                "get_status", "get_version", "get_supported_actions",
            }
            return self._adapter.make_response(data=sorted(actions))

    async def start(self):
        self._running = True

        if not self.enabled_accounts:
            self.logger.warning(i18n.t("matrix.no_enabled_accounts", default="没有已启用的账户，Matrix 适配器将以空闲状态启动"))
            return

        for account_name, account in self.enabled_accounts.items():
            # 为每个账户初始化运行时状态与 Converter
            self._account_runtime[account_name] = {
                "bot_id": "",
                "access_token": account.access_token or "",
                "_next_batch": None,
                "_dm_rooms": {},
            }
            if account_name not in self._converters:
                self._converters[account_name] = MatrixConverter()

            await self._login_if_needed(account_name, account)

            runtime = self._get_runtime(account_name)
            if not runtime.get("bot_id"):
                self.logger.error(
                    i18n.t("matrix.no_bot_id", name=account_name, default="账户 {name} 无法获取 bot user_id，请检查配置")
                )
                continue

            # E2EE 会话需在 sync 循环启动前就绪（nio 依赖它维护房间加密状态）
            await self._init_e2ee_session(account_name, account)

            await self.emit_meta("connect", runtime.get("bot_id", ""))
            hb_coro = self._heartbeat_meta_loop(account_name)
            sync_coro = self._sync_loop(account_name)
            # 生命周期任务使用 spawn_background（owner 归属，shutdown 自动回收）
            self._heartbeat_meta_tasks[account_name] = (
                spawn_background(hb_coro) if spawn_background is not None else asyncio.create_task(hb_coro)
            )
            self._sync_tasks[account_name] = (
                spawn_background(sync_coro) if spawn_background is not None else asyncio.create_task(sync_coro)
            )
            self.logger.info(
                i18n.t("matrix.account_started", name=account_name, bot_id=runtime.get('bot_id'), default="账户 {name} (bot_id: {bot_id}) Matrix 已启动")
            )

        self.logger.info(
            i18n.t("matrix.adapter_started", count=len(self.enabled_accounts), default="Matrix 适配器启动完成，共 {count} 个账户")
        )

    async def shutdown(self):
        self._running = False

        for account_name, runtime in list(self._account_runtime.items()):
            bot_id = runtime.get("bot_id", "")
            if bot_id:
                try:
                    await self.emit_meta("disconnect", bot_id)
                except Exception:
                    pass

        for task in self._heartbeat_meta_tasks.values():
            if not task.done():
                task.cancel()
        if self._heartbeat_meta_tasks:
            await asyncio.gather(
                *self._heartbeat_meta_tasks.values(), return_exceptions=True
            )
        self._heartbeat_meta_tasks.clear()

        for task in self._sync_tasks.values():
            if not task.done():
                task.cancel()
        if self._sync_tasks:
            await asyncio.gather(*self._sync_tasks.values(), return_exceptions=True)
        self._sync_tasks.clear()

        for account_name in list(self._nio_sessions.keys()):
            await self._teardown_e2ee_session(account_name)

        self._account_runtime.clear()
        try:
            unregister_platform_event_methods("matrix")
        except Exception:
            pass
        self.logger.info(i18n.t("matrix.adapter_shutdown", default="Matrix 适配器已关闭"))
