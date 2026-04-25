import asyncio
import aiohttp
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from ErisPulse import sdk
from .Converter import MatrixConverter
from ErisPulse.Core.Event import register_event_mixin, unregister_platform_event_methods


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


class MatrixAdapter(sdk.BaseAdapter):
    class Send(sdk.BaseAdapter.Send):
        def __init__(self, adapter, target_type=None, target_id=None, account_id=None):
            super().__init__(adapter, target_type, target_id, account_id)
            self._reply_event_id = None
            self._mention_users = []
            self._mention_all = False

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
            return self.Raw_ob12([{"type": "html", "data": {"html": html, "fallback": fallback}}])

        def Reply(self, message_id: str) -> "Send":
            self._reply_event_id = message_id
            return self

        def At(self, user_id: str) -> "Send":
            self._mention_users.append(user_id)
            return self

        def AtAll(self) -> "Send":
            self._mention_all = True
            return self

        def Raw_ob12(self, message: List[Dict], **kwargs):
            return asyncio.create_task(self._do_send_raw_ob12(message, **kwargs))

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
                    self._reply_event_id = data.get("message_id", "")
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

            if self._mention_users:
                text_parts.insert(0, " ".join(self._mention_users) + " ")
            if self._mention_all:
                text_parts.insert(0, "@room ")

            full_text = "".join(text_parts) or fallback_text or " "
            full_html = "".join(html_parts) if html_parts else None

            target_room = self._target_id
            content = None

            if media_file and isinstance(media_file, bytes):
                mxc_uri = await self._adapter._upload_media(media_file, media_type)
                if not mxc_uri:
                    return {
                        "status": "failed",
                        "retcode": 32000,
                        "data": None,
                        "message_id": "",
                        "message": "媒体上传失败",
                        "matrix_raw": None,
                    }
                content = self._build_media_content(media_type, mxc_uri, media_filename or full_text)
            elif media_file and isinstance(media_file, str) and media_file.startswith("mxc://"):
                content = self._build_media_content(media_type, media_file, media_filename or full_text)
            elif media_file and isinstance(media_file, str) and media_file.startswith(("http://", "https://")):
                result = await self._adapter._download_file(media_file)
                if not result:
                    return {
                        "status": "failed",
                        "retcode": 32000,
                        "data": None,
                        "message_id": "",
                        "message": "媒体下载失败",
                        "matrix_raw": None,
                    }
                file_bytes, download_ct = result
                upload_ct = download_ct if download_ct and "text/" not in download_ct else None
                mxc_uri = await self._adapter._upload_media(file_bytes, media_type, content_type=upload_ct)
                if not mxc_uri:
                    return {
                        "status": "failed",
                        "retcode": 32000,
                        "data": None,
                        "message_id": "",
                        "message": "媒体上传失败",
                        "matrix_raw": None,
                    }
                mimetype = upload_ct
                content = self._build_media_content(media_type, mxc_uri, media_filename or full_text, mimetype=mimetype)
            elif media_file:
                path = Path(str(media_file))
                if path.is_file():
                    file_bytes = path.read_bytes()
                    guessed_type, _ = mimetypes.guess_type(str(path))
                    mxc_uri = await self._adapter._upload_media(file_bytes, media_type, content_type=guessed_type)
                    if not mxc_uri:
                        return {
                            "status": "failed",
                            "retcode": 32000,
                            "data": None,
                            "message_id": "",
                            "message": "媒体上传失败",
                            "matrix_raw": None,
                        }
                    content = self._build_media_content(media_type, mxc_uri, media_filename or path.name or full_text, mimetype=guessed_type)

            if content is None:
                content = {
                    "msgtype": "m.notice" if is_notice else "m.text",
                    "body": full_text,
                }
                if full_html:
                    content["format"] = "org.matrix.custom.html"
                    content["formatted_body"] = full_html

            if self._reply_event_id:
                content["m.relates_to"] = {
                    "rel_type": "m.in_reply_to",
                    "event_id": self._reply_event_id,
                }
                self._reply_event_id = None

            all_mentioned = list(set(self._mention_users + segment_mentions))
            if all_mentioned or self._mention_all:
                content["m.mentions"] = {}
                if all_mentioned:
                    content["m.mentions"]["user_ids"] = all_mentioned
                if self._mention_all:
                    content["m.mentions"]["room"] = True

            txn_id = str(uuid.uuid4())
            endpoint = f"/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_id}"

            return await self._adapter.call_api(endpoint=endpoint, method="PUT", **content)

        def _build_media_content(self, media_type: str, mxc_uri: str, body: str, mimetype: str = None) -> Dict:
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

    def __init__(self, sdk):
        super().__init__()
        self.sdk = sdk
        self.logger = sdk.logger
        self.config = self._load_config()
        self.homeserver = self.config.get("homeserver", "https://matrix.org").rstrip("/")
        self.access_token = self.config.get("access_token", "")
        self.bot_id = ""
        self.session: Optional[aiohttp.ClientSession] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._heartbeat_meta_task: Optional[asyncio.Task] = None
        self._next_batch: Optional[str] = None
        self._dm_rooms: Dict[str, str] = {}
        self._running = False

        self.converter = MatrixConverter()
        self.convert = self.converter.convert

        if not self.access_token and self.config.get("user_id") and self.config.get("password"):
            pass

    def _load_config(self):
        config = self.sdk.config.getConfig("Matrix_Adapter")
        if not config:
            default_config = {
                "homeserver": "https://matrix.org",
                "access_token": "YOUR_ACCESS_TOKEN",
                "user_id": "",
                "password": "",
            }
            try:
                self.sdk.config.setConfig("Matrix_Adapter", default_config)
                self.logger.warning("Matrix适配器配置不存在，已自动创建默认配置")
            except Exception as e:
                self.logger.error(f"保存默认配置失败: {e}")
            return default_config
        return config

    async def _login_if_needed(self):
        if self.access_token:
            try:
                result = await self.call_api(endpoint="/_matrix/client/v3/account/whoami", method="GET")
                if result.get("status") == "ok" and result.get("data"):
                    self.bot_id = result["data"].get("user_id", "")
                    self.converter.bot_user_id = self.bot_id
                    self.logger.info(f"Matrix 已认证: {self.bot_id}")
                    return
            except Exception:
                pass

        user_id = self.config.get("user_id", "")
        password = self.config.get("password", "")
        if user_id and password:
            try:
                login_data = {
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": user_id},
                    "password": password,
                }
                result = await self.call_api(endpoint="/_matrix/client/v3/login", method="POST", **login_data)
                if result.get("status") == "ok" and result.get("data"):
                    self.access_token = result["data"].get("access_token", "")
                    self.bot_id = result["data"].get("user_id", user_id)
                    self.converter.bot_user_id = self.bot_id
                    self.logger.info(f"Matrix 登录成功: {self.bot_id}")
                else:
                    raise Exception(f"登录失败: {result.get('message', 'Unknown error')}")
            except Exception as e:
                self.logger.error(f"Matrix 登录失败: {e}")
                raise

    async def _sync_loop(self):
        self._running = True
        await self._initial_sync()
        await self._discover_dm_rooms()

        while self._running:
            try:
                result = await self.call_api(
                    endpoint=f"/_matrix/client/v3/sync?since={self._next_batch}&timeout=30000",
                    method="GET",
                )

                if result.get("status") != "ok":
                    self.logger.error(f"同步失败: {result.get('message')}")
                    await asyncio.sleep(5)
                    continue

                data = result.get("data", {})
                self._next_batch = data.get("next_batch", self._next_batch)

                await self._process_sync_response(data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"同步循环异常: {e}")
                await asyncio.sleep(5)

    async def _initial_sync(self):
        result = await self.call_api(
            endpoint="/_matrix/client/v3/sync?timeout=0",
            method="GET",
        )
        if result.get("status") == "ok" and result.get("data"):
            data = result["data"]
            self._next_batch = data.get("next_batch", "")
            self.logger.info(f"初始同步完成, next_batch: {self._next_batch}")

    async def _discover_dm_rooms(self):
        result = await self.call_api(
            endpoint="/_matrix/client/v3/user/{user_id}/account_data/m.direct".format(user_id=self.bot_id),
            method="GET",
        )
        if result.get("status") == "ok" and result.get("data"):
            dm_data = result["data"]
            for user_id, room_ids in dm_data.items():
                if isinstance(room_ids, list) and room_ids:
                    self._dm_rooms[room_ids[0]] = user_id
            self.converter.set_dm_rooms(self._dm_rooms)
            self.logger.info(f"发现 {len(self._dm_rooms)} 个 DM 房间")

    async def _process_sync_response(self, data: dict):
        joined_rooms = data.get("rooms", {}).get("join", {})
        for room_id, room_data in joined_rooms.items():
            is_dm = room_id in self._dm_rooms

            timeline = room_data.get("timeline", {})
            events = timeline.get("events", [])

            for event in events:
                self.logger.debug(f"处理 Matrix 事件: {event}")
                try:
                    onebot_event = self.convert(event, room_id=room_id, is_dm=is_dm)
                    if onebot_event:
                        await self.sdk.adapter.emit(onebot_event)
                except Exception as e:
                    self.logger.error(f"处理事件失败: {e}")

        invite_rooms = data.get("rooms", {}).get("invite", {})
        for room_id, room_data in invite_rooms.items():
            invite_state = room_data.get("invite_state", {}).get("events", [])
            for event in invite_state:
                if event.get("type") == "m.room.member":
                    content = event.get("content", {})
                    if content.get("membership") == "invite" and event.get("state_key") == self.bot_id:
                        self.logger.info(f"收到房间邀请: {room_id}")
                        if self.config.get("auto_accept_invites", True):
                            try:
                                await self.call_api(
                                    endpoint=f"/_matrix/client/v3/join/{room_id}",
                                    method="POST",
                                )
                                self.logger.info(f"已自动加入房间: {room_id}")
                            except Exception as e:
                                self.logger.error(f"加入房间失败: {e}")

    async def _download_file(self, url: str) -> Optional[tuple]:
        try:
            timeout = aiohttp.ClientTimeout(total=300)
            async with self.session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    self.logger.error(f"下载文件失败: HTTP {resp.status}")
                    return None
                content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
                data = await resp.read()
                return (data, content_type)
        except Exception as e:
            self.logger.error(f"下载文件异常: {e}")
            return None

    async def _upload_media(self, file: bytes, media_type: str, content_type: str = None) -> Optional[str]:
        if not content_type:
            content_type_map = {
                "image": "image/png",
                "voice": "audio/ogg",
                "video": "video/mp4",
                "file": "application/octet-stream",
            }
            content_type = content_type_map.get(media_type, "application/octet-stream")

        url = f"{self.homeserver}/_matrix/media/v3/upload"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": content_type,
        }

        try:
            async with self.session.post(url, data=file, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data.get("content_uri", "")
                else:
                    self.logger.error(f"上传媒体失败: {data}")
                    return None
        except Exception as e:
            self.logger.error(f"上传媒体异常: {e}")
            return None

    async def call_api(self, endpoint: str, method: str = "POST", **params):
        url = f"{self.homeserver}{endpoint}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

        try:
            if method.upper() == "GET":
                async with self.session.get(url, headers=headers) as resp:
                    raw_response = await resp.json()
            elif method.upper() == "PUT":
                async with self.session.put(url, json=params, headers=headers) as resp:
                    raw_response = await resp.json()
            else:
                async with self.session.post(url, json=params, headers=headers) as resp:
                    raw_response = await resp.json()

            success = 200 <= resp.status < 300

            if not isinstance(raw_response, dict):
                return {
                    "status": "ok" if success else "failed",
                    "retcode": 0 if success else 34000,
                    "data": raw_response,
                    "message_id": "",
                    "message": "",
                    "matrix_raw": raw_response,
                }

            event_id = raw_response.get("event_id", "")
            message_id = str(event_id) if event_id else ""
            data = dict(raw_response)
            data["message_id"] = message_id

            return {
                "status": "ok" if success else "failed",
                "retcode": 0 if success else raw_response.get("errcode", 34000),
                "data": data,
                "message_id": message_id,
                "message": "" if success else raw_response.get("error", f"HTTP {resp.status}"),
                "matrix_raw": raw_response,
            }

        except asyncio.TimeoutError:
            self.logger.error(f"Matrix API 请求超时: {endpoint}")
            return {
                "status": "failed",
                "retcode": 32000,
                "data": None,
                "message_id": "",
                "message": "请求超时",
                "matrix_raw": None,
            }
        except Exception as e:
            self.logger.error(f"调用 Matrix API 失败: {e}")
            return {
                "status": "failed",
                "retcode": 33000,
                "data": None,
                "message_id": "",
                "message": f"API调用失败: {str(e)}",
                "matrix_raw": None,
            }

    async def _on_connect(self):
        await self.sdk.adapter.emit({
            "type": "meta",
            "detail_type": "connect",
            "platform": "matrix",
            "self": {"platform": "matrix", "user_id": self.bot_id},
        })
        self._heartbeat_meta_task = asyncio.create_task(self._heartbeat_meta_loop())

    async def _heartbeat_meta_loop(self):
        try:
            while self._running:
                await asyncio.sleep(30)
                await self.sdk.adapter.emit({
                    "type": "meta",
                    "detail_type": "heartbeat",
                    "platform": "matrix",
                    "self": {"platform": "matrix", "user_id": self.bot_id},
                })
        except asyncio.CancelledError:
            pass

    async def start(self):
        self.session = aiohttp.ClientSession()

        await self._login_if_needed()

        if not self.bot_id:
            self.logger.error("无法获取 bot user_id，请检查配置")
            if self.session:
                await self.session.close()
            raise Exception("Authentication failed")

        await self._on_connect()

        self._sync_task = asyncio.create_task(self._sync_loop())
        self.logger.info("Matrix 适配器已启动")

    async def shutdown(self):
        self._running = False

        if self.bot_id:
            await self.sdk.adapter.emit({
                "type": "meta",
                "detail_type": "disconnect",
                "platform": "matrix",
                "self": {"platform": "matrix", "user_id": self.bot_id},
            })

        if self._heartbeat_meta_task:
            self._heartbeat_meta_task.cancel()
            try:
                await self._heartbeat_meta_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_meta_task = None

        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        if self.session:
            await self.session.close()
            self.session = None

        self._dm_rooms.clear()
        unregister_platform_event_methods("matrix")
        self.logger.info("Matrix 适配器已关闭")
