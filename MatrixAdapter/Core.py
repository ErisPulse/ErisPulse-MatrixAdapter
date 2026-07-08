import asyncio
import mimetypes
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ErisPulse.Core import client
from ErisPulse.Core.Bases.adapter import BaseAdapter
from ErisPulse.Core.Event import register_event_mixin, unregister_platform_event_methods
from ErisPulse.runtime.config_schema import BotAccountConfig
from ErisPulse.Core.i18n import i18n

from .Converter import MatrixConverter


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
            "ui": {"widget": "password", "group": "basic", "order": 4},
        },
    )
    auto_accept_invites: bool = field(
        default=True,
        metadata={
            "description": {"i18n": "matrix.auto_accept_invites", "default": "自动接受房间邀请"},
            "ui": {"widget": "switch", "group": "behavior", "order": 5},
        },
    )


MatrixAccountConfig._schema_meta = {
    "group_labels": {
        "basic": {"i18n": "matrix.group.basic", "default": "基本设置"},
        "connection": {"i18n": "matrix.group.connection", "default": "连接设置"},
        "behavior": {"i18n": "matrix.group.behavior", "default": "行为设置"},
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
            return self.Raw_ob12([{"type": "voice", "data": {"file": file}}])

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

        def Raw_ob12(self, message: List[Dict], **kwargs):
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
                elif seg_type in ("image", "voice", "video", "file"):
                    file = data.get("file", data.get("url", ""))
                    if file:
                        media_file = file
                        media_type = seg_type
                        if data.get("filename"):
                            media_filename = data["filename"]

            # 基类内置修饰器：_at_user_ids / _at_all
            if self._at_user_ids:
                text_parts.insert(0, " ".join(self._at_user_ids) + " ")
            if self._at_all:
                text_parts.insert(0, "@room ")

            full_text = "".join(text_parts) or fallback_text or " "
            full_html = "".join(html_parts) if html_parts else None

            ctx = self.send_context
            target_room = ctx.get("target_id")
            account_id = ctx.get("account_id")
            # 多账户：解析当前发送所用账户（用于媒体上传/下载与发送）
            account_name, _ = self._adapter._resolve_account(account_id)
            content = None

            if media_file and isinstance(media_file, bytes):
                mxc_uri = await self._adapter._upload_media(
                    account_name, media_file, media_type
                )
                if not mxc_uri:
                    return self._adapter.make_error(
                        retcode=32000, message="媒体上传失败", raw=None
                    )
                content = self._build_media_content(
                    media_type, mxc_uri, media_filename or full_text
                )
            elif (
                media_file
                and isinstance(media_file, str)
                and media_file.startswith("mxc://")
            ):
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
                        retcode=32000, message="媒体下载失败", raw=None
                    )
                file_bytes, download_ct = result
                upload_ct = (
                    download_ct if download_ct and "text/" not in download_ct else None
                )
                mxc_uri = await self._adapter._upload_media(
                    account_name, file_bytes, media_type, content_type=upload_ct
                )
                if not mxc_uri:
                    return self._adapter.make_error(
                        retcode=32000, message="媒体上传失败", raw=None
                    )
                mimetype = upload_ct
                content = self._build_media_content(
                    media_type, mxc_uri, media_filename or full_text, mimetype=mimetype
                )
            elif media_file:
                path = Path(str(media_file))
                if path.is_file():
                    file_bytes = path.read_bytes()
                    guessed_type, _ = mimetypes.guess_type(str(path))
                    mxc_uri = await self._adapter._upload_media(
                        account_name, file_bytes, media_type, content_type=guessed_type
                    )
                    if not mxc_uri:
                        return self._adapter.make_error(
                            retcode=32000, message="媒体上传失败", raw=None
                        )
                    content = self._build_media_content(
                        media_type,
                        mxc_uri,
                        media_filename or path.name or full_text,
                        mimetype=guessed_type,
                    )

            if content is None:
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

            txn_id = str(uuid.uuid4())
            endpoint = (
                f"/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_id}"
            )

            return await self._adapter.call_api(
                endpoint=endpoint, method="PUT", _account_id=account_id, **content
            )

        def _build_media_content(
            self, media_type: str, mxc_uri: str, body: str, mimetype: str = None
        ) -> Dict:
            msgtype_map = {
                "image": "m.image",
                "voice": "m.audio",
                "video": "m.video",
                "file": "m.file",
            }
            if not mimetype:
                mimetype_map = {
                    "image": "image/png",
                    "voice": "audio/ogg",
                    "video": "video/mp4",
                    "file": "application/octet-stream",
                }
                mimetype = mimetype_map.get(media_type, "application/octet-stream")
            return {
                "msgtype": msgtype_map.get(media_type, "m.file"),
                "body": body or media_type,
                "url": mxc_uri,
                "info": {
                    "mimetype": mimetype,
                },
            }

    # ---- 适配器方法 ----
    def __init__(self, sdk_ref=None):
        super().__init__(sdk_ref)
        # 每个账户的运行时状态（登录后获得，不属于配置）
        # _account_runtime[name] = {bot_id, access_token, _next_batch, _dm_rooms}
        self._account_runtime: Dict[str, dict] = {}
        self._sync_tasks: Dict[str, asyncio.Task] = {}
        self._heartbeat_meta_tasks: Dict[str, asyncio.Task] = {}
        self._converters: Dict[str, MatrixConverter] = {}
        self._running = False
        self._register_i18n()

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
                "matrix.group.basic": "Basic",
                "matrix.group.connection": "Connection",
                "matrix.group.behavior": "Behavior",
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
        }
        try:
            i18n.register("zh-CN", zh_CN, domain="matrix")
            i18n.register("en", en, domain="matrix")
        except Exception:
            pass

    def _get_config_key(self) -> str:
        return "Matrix_Adapter"

    def _get_runtime(self, name: str) -> dict:
        if name not in self._account_runtime:
            self._account_runtime[name] = {
                "bot_id": "",
                "access_token": "",
                "_next_batch": None,
                "_dm_rooms": {},
            }
        return self._account_runtime[name]

    def _load_accounts(self) -> dict:
        from ErisPulse.Core.config import config as config_mgr
        from ErisPulse.runtime.config_schema import dict_to_dataclass

        key = "Matrix_Adapter.accounts"
        data = config_mgr.getConfig(key)
        if not data:
            # 兼容旧配置：如果存在旧的单账户 Matrix_Adapter 配置且有 access_token，迁移为 default 账户
            old = config_mgr.getConfig("Matrix_Adapter")
            if old and old.get("access_token"):
                self.logger.warning(
                    i18n.t("matrix.old_config_detected", default="检测到旧格式单账户配置，建议迁移到 Matrix_Adapter.accounts.default")
                )
                data = {"default": {**old, "enabled": True}}
            else:
                data = {
                    "default": {
                        "homeserver": "https://matrix.org",
                        "access_token": "",
                        "user_id": "",
                        "password": "",
                        "auto_accept_invites": True,
                        "enabled": True,
                    }
                }
            try:
                config_mgr.setConfig(key, data)
            except Exception as e:
                self.logger.error(i18n.t("matrix.save_default_failed", error=e, default="保存默认配置失败: {error}"))
        accounts = {}
        for name, account_data in data.items():
            if not isinstance(account_data, dict):
                continue
            if not account_data.get("access_token") and not account_data.get("user_id"):
                self.logger.warning(i18n.t("matrix.missing_credentials", name=name, default="账户 '{name}' 缺少 access_token/user_id，已跳过"))
                continue
            instance = dict_to_dataclass(MatrixAccountConfig, account_data)
            instance.name = name
            if not instance.enabled:
                self.logger.warning(
                    i18n.t("matrix.account_disabled", name=name, default="账户 '{name}' 已加载但未启用（enabled=false），请在配置中将 [Matrix_Adapter.accounts.{name}] 的 enabled 设为 true")
                )
            accounts[name] = instance
        self.logger.info(i18n.t("matrix.accounts_loaded", count=len(accounts), default="Matrix适配器初始化完成，共加载 {count} 个账户"))
        return accounts

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
                result = await self.call_api(
                    endpoint=f"/_matrix/client/v3/sync?since={runtime.get('_next_batch') or ''}&timeout=30000",
                    method="GET",
                    _account_id=account_name,
                )

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
        result = await self.call_api(
            endpoint="/_matrix/client/v3/sync?timeout=0",
            method="GET",
            _account_id=account_name,
        )
        if result.get("status") == "ok" and result.get("data"):
            data = result["data"]
            runtime["_next_batch"] = data.get("next_batch", "")
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
        account_name, account = self._resolve_account(_account_id)
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
                    retcode=0 if success else 34000,
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

            response = self.make_response(
                status="ok" if success else "failed",
                retcode=0 if success else raw_response.get("errcode", 34000),
                data=data,
                message_id=message_id,
                message=""
                if success
                else raw_response.get("error", f"HTTP {resp.status}"),
                raw=raw_response,
            )
            response["matrix_raw"] = raw_response
            response["http_status"] = resp.status
            if echo:
                response["echo"] = echo
            return response

        except asyncio.TimeoutError:
            self.logger.error(i18n.t("matrix.api_timeout", name=account_name, endpoint=endpoint, default="账户 {name} Matrix API 请求超时: {endpoint}"))
            err = self.make_error(retcode=32000, message=i18n.t("matrix.timeout_msg", default="请求超时"), raw=None)
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

            await self.emit_meta("connect", runtime.get("bot_id", ""))
            self._heartbeat_meta_tasks[account_name] = asyncio.create_task(
                self._heartbeat_meta_loop(account_name)
            )
            self._sync_tasks[account_name] = asyncio.create_task(
                self._sync_loop(account_name)
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

        self._account_runtime.clear()
        try:
            unregister_platform_event_methods("matrix")
        except Exception:
            pass
        self.logger.info(i18n.t("matrix.adapter_shutdown", default="Matrix 适配器已关闭"))
