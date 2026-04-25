import time
import uuid
from typing import Dict, Optional, List


class MatrixConverter:
    def __init__(self, bot_user_id: str = ""):
        self.bot_user_id = bot_user_id
        self._dm_rooms: Dict[str, str] = {}

    def set_dm_rooms(self, dm_rooms: Dict[str, str]):
        self._dm_rooms = dm_rooms

    def convert(self, raw_event: Dict, room_id: str = "", is_dm: bool = False) -> Optional[Dict]:
        if not isinstance(raw_event, dict):
            return None

        event_type = raw_event.get("type", "")
        event_id = raw_event.get("event_id", str(uuid.uuid4()))

        origin_server_ts = raw_event.get("origin_server_ts", 0)
        if origin_server_ts:
            if origin_server_ts > 10**12:
                event_time = int(origin_server_ts / 1000)
            else:
                event_time = int(origin_server_ts)
        else:
            event_time = int(time.time())

        sender = raw_event.get("sender", "")
        if sender == self.bot_user_id:
            return None

        base_event = {
            "id": event_id,
            "time": event_time,
            "type": "",
            "detail_type": "",
            "platform": "matrix",
            "self": {
                "platform": "matrix",
                "user_id": self.bot_user_id,
            },
            "matrix_raw": raw_event,
            "matrix_raw_type": event_type,
        }

        if is_dm:
            base_event["detail_type"] = "private"
        else:
            base_event["detail_type"] = "group"

        handler = getattr(self, f"_handle_{event_type.replace('.', '_')}", None)
        if handler:
            return handler(raw_event, base_event, room_id, is_dm)

        if event_type.startswith("m.room."):
            return self._handle_m_room_generic(raw_event, base_event, room_id, is_dm)

        return self._create_unknown_event(raw_event, event_id, event_type)

    def _handle_m_room_message(self, raw_event: Dict, base_event: Dict, room_id: str, is_dm: bool) -> Dict:
        base_event["type"] = "message"
        base_event["user_id"] = raw_event.get("sender", "")
        base_event["user_nickname"] = raw_event.get("sender", "")

        content = raw_event.get("content", {})
        msgtype = content.get("msgtype", "m.text")

        message_segments = self._parse_message_content(content, raw_event)
        alt_message = self._generate_alt_message(message_segments)

        base_event["message"] = message_segments
        base_event["alt_message"] = alt_message

        if room_id:
            base_event["matrix_room_id"] = room_id
            if is_dm:
                pass
            else:
                base_event["group_id"] = room_id

        relates_to = content.get("m.relates_to", {})
        if relates_to:
            rel_type = relates_to.get("rel_type", "")
            if rel_type == "m.in_reply_to":
                reply_event_id = relates_to.get("event_id", "")
                if reply_event_id:
                    reply_seg = {"type": "reply", "data": {"message_id": reply_event_id}}
                    base_event["message"].insert(0, reply_seg)
            elif rel_type == "m.thread":
                base_event["thread_id"] = relates_to.get("event_id", "")

        new_content = content.get("m.new_content")
        if new_content:
            base_event["matrix_edit"] = True
            base_event["matrix_original_event_id"] = relates_to.get("event_id", "")

        return base_event

    def _handle_m_room_member(self, raw_event: Dict, base_event: Dict, room_id: str, is_dm: bool) -> Dict:
        base_event["type"] = "notice"
        base_event["user_id"] = raw_event.get("sender", "")
        base_event["user_nickname"] = raw_event.get("sender", "")

        content = raw_event.get("content", {})
        prev_content = raw_event.get("unsigned", {}).get("prev_content", {})
        membership = content.get("membership", "")
        prev_membership = prev_content.get("membership", "")

        state_key = raw_event.get("state_key", "")
        target_user = state_key

        if membership == "join" and prev_membership != "join":
            base_event["detail_type"] = "group_member_increase"
            base_event["user_id"] = target_user
            displayname = content.get("displayname", target_user)
            base_event["user_nickname"] = displayname
            base_event["operator_id"] = raw_event.get("sender", "")
            base_event["group_id"] = room_id
            base_event["matrix_room_id"] = room_id
        elif membership in ("leave", "ban") and prev_membership == "join":
            base_event["detail_type"] = "group_member_decrease"
            base_event["user_id"] = target_user
            base_event["user_nickname"] = content.get("displayname", target_user)
            base_event["operator_id"] = raw_event.get("sender", "")
            base_event["group_id"] = room_id
            base_event["matrix_room_id"] = room_id
        else:
            base_event["detail_type"] = "matrix_member_update"
            base_event["user_id"] = target_user
            base_event["group_id"] = room_id
            base_event["matrix_room_id"] = room_id
            base_event["matrix_membership"] = membership

        return base_event

    def _handle_m_reaction(self, raw_event: Dict, base_event: Dict, room_id: str, is_dm: bool) -> Dict:
        base_event["type"] = "notice"
        base_event["detail_type"] = "matrix_reaction"
        base_event["user_id"] = raw_event.get("sender", "")
        base_event["matrix_room_id"] = room_id
        if not is_dm:
            base_event["group_id"] = room_id

        content = raw_event.get("content", {})
        relates_to = content.get("m.relates_to", {})
        base_event["matrix_reaction_event_id"] = relates_to.get("event_id", "")
        base_event["matrix_reaction_key"] = relates_to.get("key", "")

        return base_event

    def _handle_m_room_redaction(self, raw_event: Dict, base_event: Dict, room_id: str, is_dm: bool) -> Dict:
        base_event["type"] = "notice"
        base_event["detail_type"] = "matrix_redaction"
        base_event["user_id"] = raw_event.get("sender", "")
        base_event["matrix_room_id"] = room_id
        if not is_dm:
            base_event["group_id"] = room_id

        redacts = raw_event.get("redacts", "")
        base_event["matrix_redacted_event_id"] = redacts

        return base_event

    def _handle_m_room_generic(self, raw_event: Dict, base_event: Dict, room_id: str, is_dm: bool) -> Dict:
        event_type = raw_event.get("type", "")
        base_event["type"] = "notice"
        base_event["detail_type"] = f"matrix_{event_type.replace('.', '_').replace('m_room_', '')}"
        base_event["user_id"] = raw_event.get("sender", "")
        base_event["matrix_room_id"] = room_id
        if not is_dm:
            base_event["group_id"] = room_id

        return base_event

    def _parse_message_content(self, content: Dict, raw_event: Dict) -> List[Dict]:
        segments = []
        msgtype = content.get("msgtype", "m.text")
        body = content.get("body", "")
        formatted_body = content.get("formatted_body", "")

        if msgtype in ("m.text", "m.notice", "m.emote"):
            seg_data = {"text": body}
            if formatted_body:
                seg_data["html"] = formatted_body
            segments.append({"type": "text", "data": seg_data})

        elif msgtype == "m.image":
            url = content.get("url", "")
            segments.append({
                "type": "image",
                "data": {
                    "url": url,
                    "filename": body,
                    "matrix_mxc": url,
                    "info": content.get("info", {}),
                },
            })

        elif msgtype == "m.audio":
            url = content.get("url", "")
            segments.append({
                "type": "voice",
                "data": {
                    "url": url,
                    "filename": body,
                    "matrix_mxc": url,
                    "info": content.get("info", {}),
                },
            })

        elif msgtype == "m.video":
            url = content.get("url", "")
            segments.append({
                "type": "video",
                "data": {
                    "url": url,
                    "filename": body,
                    "matrix_mxc": url,
                    "info": content.get("info", {}),
                },
            })

        elif msgtype == "m.file":
            url = content.get("url", "")
            segments.append({
                "type": "file",
                "data": {
                    "url": url,
                    "filename": body,
                    "matrix_mxc": url,
                    "info": content.get("info", {}),
                },
            })

        elif msgtype == "m.location":
            geo_uri = content.get("geo_uri", "")
            segments.append({
                "type": "location",
                "data": {
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "matrix_geo_uri": geo_uri,
                    "text": body,
                },
            })

        return segments

    def _create_unknown_event(self, raw_event: Dict, event_id: str, event_type: str) -> Dict:
        return {
            "id": event_id,
            "time": int(time.time()),
            "type": "unknown",
            "detail_type": "unknown",
            "platform": "matrix",
            "self": {"platform": "matrix", "user_id": self.bot_user_id},
            "matrix_raw": raw_event,
            "matrix_raw_type": event_type,
            "warning": f"Unsupported event type: {event_type}",
            "alt_message": "This event type is not supported by this system.",
        }

    def _generate_alt_message(self, segments: List[Dict]) -> str:
        parts = []
        for seg in segments:
            seg_type = seg["type"]
            data = seg.get("data", {})
            if seg_type == "text":
                parts.append(data.get("text", ""))
            elif seg_type == "image":
                parts.append("[图片]")
            elif seg_type == "voice":
                parts.append("[语音]")
            elif seg_type == "video":
                parts.append("[视频]")
            elif seg_type == "file":
                parts.append(f"[文件:{data.get('filename', '')}]")
            elif seg_type == "location":
                parts.append("[位置]")
            elif seg_type == "reply":
                parts.append("[回复]")
        return " ".join(parts).strip()
