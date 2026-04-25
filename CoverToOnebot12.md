# MatrixAdapter与OneBot12协议的转换对照

## Matrix特有事件类型

Matrix平台提供以下事件类型，可在消息处理中检测使用：

### 1. 消息事件

| Matrix事件类型 | 说明 | 转换后 |
|---|---|---|
| m.room.message (m.text) | 文本消息 | OneBot12 `message` 事件，`detail_type` 为 `private` 或 `group` |
| m.room.message (m.notice) | 通知消息 | OneBot12 `message` 事件，`detail_type` 为 `private` 或 `group` |
| m.room.message (m.emote) | 动作消息 | OneBot12 `message` 事件，`detail_type` 为 `private` 或 `group` |
| m.room.message (m.image) | 图片消息 | OneBot12 `message` 事件，含 `image` 消息段 |
| m.room.message (m.audio) | 音频消息 | OneBot12 `message` 事件，含 `voice` 消息段 |
| m.room.message (m.video) | 视频消息 | OneBot12 `message` 事件，含 `video` 消息段 |
| m.room.message (m.file) | 文件消息 | OneBot12 `message` 事件，含 `file` 消息段 |
| m.room.message (m.location) | 位置消息 | OneBot12 `message` 事件，含 `location` 消息段 |

### 2. 通知事件

| Matrix事件类型 | 说明 | 转换后 |
|---|---|---|
| m.room.member (join) | 用户加入房间 | OneBot12 `notice` 事件，`detail_type` 为 `group_member_increase` |
| m.room.member (leave/ban) | 用户离开/被封禁 | OneBot12 `notice` 事件，`detail_type` 为 `group_member_decrease` |
| m.room.member (其他) | 成员信息更新 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_member_update` |
| m.reaction | 表情回应 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_reaction` |
| m.room.redaction | 消息撤回/删除 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_redaction` |
| m.room.* (其他) | 其他房间事件 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_{type}` |

### 3. Matrix平台特有通知事件

| Matrix事件类型 | 说明 | 转换后 |
|---|---|---|
| m.reaction | 消息表情回应 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_reaction` |
| m.room.redaction | 消息撤回 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_redaction` |
| m.room.name | 房间名称变更 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_name` |
| m.room.topic | 房间话题变更 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_topic` |
| m.room.avatar | 房间头像变更 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_avatar` |
| m.room.power_levels | 权限等级变更 | OneBot12 `notice` 事件，`detail_type` 为 `matrix_power_levels` |

### 4. 消息中的特殊关系

| 关系类型 | 说明 | 处理方式 |
|---|---|---|
| m.in_reply_to | 回复消息 | 在 `message` 列表头部插入 `reply` 类型消息段 |
| m.thread | 线程消息 | 添加 `thread_id` 字段 |
| m.replace (编辑) | 消息编辑 | 添加 `matrix_edit` 和 `matrix_original_event_id` 字段 |

### 事件处理示例

```python
from ErisPulse.Core.Event import notice, message

# 处理消息事件
@message.on_message()
async def handle_message(event):
    if event.get("platform") != "matrix":
        return

    detail_type = event.get("detail_type")

    if detail_type == "private":
        text = event.get_text()
        # 处理私聊消息...
    elif detail_type == "group":
        # 处理群组消息...
        group_id = event.get("group_id")

# 处理通知事件
@notice.on_notice()
async def handle_notice(event):
    if event.get("platform") != "matrix":
        return

    detail_type = event.get("detail_type")

    if detail_type == "group_member_increase":
        user_id = event.get("user_id")
        group_id = event.get("group_id")
    elif detail_type == "group_member_decrease":
        user_id = event.get("user_id")
        operator_id = event.get("operator_id")
    elif detail_type == "matrix_reaction":
        reaction_key = event.get("matrix_reaction_key")
        reacted_event_id = event.get("matrix_reaction_event_id")
    elif detail_type == "matrix_redaction":
        redacted_event_id = event.get("matrix_redacted_event_id")
    elif detail_type == "matrix_member_update":
        membership = event.get("matrix_membership")
```

---

## 消息事件转换对照

### 1. 私聊文本消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$event_id_example",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.text",
    "body": "Hello"
  }
}
```

转换后:
```json
{
  "id": "$event_id_example",
  "time": 1745558400,
  "type": "message",
  "detail_type": "private",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": {
    "type": "m.room.message",
    "event_id": "$event_id_example",
    "sender": "@user:matrix.org",
    "origin_server_ts": 1745558400000,
    "content": {
      "msgtype": "m.text",
      "body": "Hello"
    }
  },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "message": [
    {
      "type": "text",
      "data": {
        "text": "Hello"
      }
    }
  ],
  "alt_message": "Hello"
}
```

### 2. 群组文本消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$group_msg_id_example",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.text",
    "body": "你好"
  }
}
```

转换后:
```json
{
  "id": "$group_msg_id_example",
  "time": 1745558400,
  "type": "message",
  "detail_type": "group",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": {
    "type": "m.room.message",
    "event_id": "$group_msg_id_example",
    "sender": "@user:matrix.org",
    "origin_server_ts": 1745558400000,
    "content": {
      "msgtype": "m.text",
      "body": "你好"
    }
  },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "message": [
    {
      "type": "text",
      "data": {
        "text": "你好"
      }
    }
  ],
  "alt_message": "你好"
}
```

### 3. 带HTML格式的消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$html_msg_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.text",
    "body": "Hello World",
    "format": "org.matrix.custom.html",
    "formatted_body": "<b>Hello</b> <i>World</i>"
  }
}
```

转换后:
```json
{
  "id": "$html_msg_id",
  "time": 1745558400,
  "type": "message",
  "detail_type": "group",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "message": [
    {
      "type": "text",
      "data": {
        "text": "Hello World",
        "html": "<b>Hello</b> <i>World</i>"
      }
    }
  ],
  "alt_message": "Hello World"
}
```

### 4. 图片消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$image_msg_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.image",
    "body": "photo.png",
    "url": "mxc://matrix.org/abc123",
    "info": {
      "mimetype": "image/png",
      "w": 800,
      "h": 600,
      "size": 123456
    }
  }
}
```

转换后:
```json
{
  "id": "$image_msg_id",
  "time": 1745558400,
  "type": "message",
  "detail_type": "group",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "message": [
    {
      "type": "image",
      "data": {
        "url": "mxc://matrix.org/abc123",
        "filename": "photo.png",
        "matrix_mxc": "mxc://matrix.org/abc123",
        "info": {
          "mimetype": "image/png",
          "w": 800,
          "h": 600,
          "size": 123456
        }
      }
    }
  ],
  "alt_message": "[图片]"
}
```

### 5. 回复消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$reply_msg_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.text",
    "body": "回复内容",
    "m.relates_to": {
      "rel_type": "m.in_reply_to",
      "event_id": "$original_msg_id"
    }
  }
}
```

转换后:
```json
{
  "id": "$reply_msg_id",
  "time": 1745558400,
  "type": "message",
  "detail_type": "group",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "message": [
    {
      "type": "reply",
      "data": {
        "message_id": "$original_msg_id"
      }
    },
    {
      "type": "text",
      "data": {
        "text": "回复内容"
      }
    }
  ],
  "alt_message": "[回复] 回复内容"
}
```

### 6. 成员加入事件（m.room.member）

原始事件:
```json
{
  "type": "m.room.member",
  "event_id": "$member_join_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "state_key": "@newuser:matrix.org",
  "content": {
    "membership": "join",
    "displayname": "新用户"
  },
  "unsigned": {
    "prev_content": {
      "membership": "invite"
    }
  }
}
```

转换后:
```json
{
  "id": "$member_join_id",
  "time": 1745558400,
  "type": "notice",
  "detail_type": "group_member_increase",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.member",
  "user_id": "@newuser:matrix.org",
  "user_nickname": "新用户",
  "operator_id": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org"
}
```

### 7. 表情回应事件（m.reaction）

原始事件:
```json
{
  "type": "m.reaction",
  "event_id": "$reaction_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "m.relates_to": {
      "rel_type": "m.annotation",
      "event_id": "$reacted_msg_id",
      "key": "👍"
    }
  }
}
```

转换后:
```json
{
  "id": "$reaction_id",
  "time": 1745558400,
  "type": "notice",
  "detail_type": "matrix_reaction",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.reaction",
  "user_id": "@user:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_reaction_event_id": "$reacted_msg_id",
  "matrix_reaction_key": "👍"
}
```

### 8. 消息撤回事件（m.room.redaction）

原始事件:
```json
{
  "type": "m.room.redaction",
  "event_id": "$redaction_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "redacts": "$redacted_msg_id"
}
```

转换后:
```json
{
  "id": "$redaction_id",
  "time": 1745558400,
  "type": "notice",
  "detail_type": "matrix_redaction",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.redaction",
  "user_id": "@user:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_redacted_event_id": "$redacted_msg_id"
}
```

### 9. 线程消息

原始事件:
```json
{
  "type": "m.room.message",
  "event_id": "$thread_msg_id",
  "sender": "@user:matrix.org",
  "origin_server_ts": 1745558400000,
  "content": {
    "msgtype": "m.text",
    "body": "线程中的回复",
    "m.relates_to": {
      "rel_type": "m.thread",
      "event_id": "$thread_root_id"
    }
  }
}
```

转换后:
```json
{
  "id": "$thread_msg_id",
  "time": 1745558400,
  "type": "message",
  "detail_type": "group",
  "platform": "matrix",
  "self": {
    "platform": "matrix",
    "user_id": "@bot:matrix.org"
  },
  "matrix_raw": { "...": "原始事件内容" },
  "matrix_raw_type": "m.room.message",
  "user_id": "@user:matrix.org",
  "user_nickname": "@user:matrix.org",
  "group_id": "!room_id:matrix.org",
  "matrix_room_id": "!room_id:matrix.org",
  "thread_id": "$thread_root_id",
  "message": [
    {
      "type": "text",
      "data": {
        "text": "线程中的回复"
      }
    }
  ],
  "alt_message": "线程中的回复"
}
```

---

## MatrixAdapter发送消息类型（OneBot12扩展）

MatrixAdapter适配器支持使用 OneBot12 消息段格式发送消息，支持以下类型：

### 1. 基础消息类型

| 类型 | 说明 | 参数 | Matrix msgtype |
|------|------|------|----------------|
| `text` | 纯文本 | `text`: 文本内容 | m.text |
| `notice` | 通知文本 | `text`: 文本内容 | m.notice |
| `html` | HTML格式 | `html`: HTML内容, `fallback`: 纯文本回退 | m.text (带 formatted_body) |

### 2. 媒体消息类型

| 类型 | 说明 | 参数 | Matrix msgtype |
|------|------|------|----------------|
| `image` | 图片 | `file`: 文件路径/URL/bytes/mxc:// | m.image |
| `voice` | 语音 | `file`: 文件路径/URL/bytes/mxc:// | m.audio |
| `video` | 视频 | `file`: 文件路径/URL/bytes/mxc:// | m.video |
| `file` | 文件 | `file`: 文件路径/URL/bytes/mxc:// | m.file |

> 媒体消息支持多种输入格式：
> - `bytes`：二进制数据，自动上传到 Matrix 服务器
> - `str`（mxc://）：Matrix URI，直接使用
> - `str`（http:///https://）：网络地址，先下载再上传
> - `str`（本地路径）：本地文件，读取后上传

### 3. Matrix特有类型

| 类型 | 说明 | 参数 |
|------|------|------|
| `reply` | 回复消息 | `message_id`: 事件ID（通过链式修饰 `.Reply()` 设置） |
| `mention` | @用户 | `user_id`: 用户ID（通过链式修饰 `.At()` 设置） |

### 4. 使用链式调用发送

```python
from ErisPulse import sdk
matrix = sdk.adapter.get("matrix")

# 基础发送
await matrix.Send.To("user", room_id).Text("Hello")

# 发送带@的消息
await matrix.Send.To("group", room_id).At("@user:matrix.org").Text("@成员")

# 发送带@所有人的消息
await matrix.Send.To("group", room_id).AtAll().Text("公告通知")

# 发送回复消息
await matrix.Send.To("group", room_id).Reply("$event_id").Text("回复内容")

# 发送通知消息
await matrix.Send.To("group", room_id).Notice("系统通知")

# 发送HTML消息
await matrix.Send.To("group", room_id).Html("<b>加粗</b>", fallback="加粗")

# 发送图片（URL）
await matrix.Send.To("group", room_id).Image("https://example.com/image.png")

# 发送图片（MXC URI）
await matrix.Send.To("group", room_id).Image("mxc://matrix.org/abc123")

# 发送图片（二进制数据）
with open("image.png", "rb") as f:
    image_bytes = f.read()
await matrix.Send.To("group", room_id).Image(image_bytes)

# 发送文件
await matrix.Send.To("group", room_id).File("/path/to/file.pdf", filename="document.pdf")

# 使用 Raw_ob12 发送复杂消息
message = [
    {"type": "text", "data": {"text": "第一行"}},
    {"type": "image", "data": {"file": "https://example.com/img.jpg"}},
    {"type": "text", "data": {"text": "第二行"}}
]
await matrix.Send.To("group", room_id).Raw_ob12(message)
```

### 5. 发送目标类型

| target_type | 说明 | 备注 |
|-------------|------|------|
| `user` | 私聊房间 | 使用 DM 房间 ID |
| `group` | 群组房间 | 使用房间 ID |

> Matrix 中私聊和群组均为房间（Room）概念，发送时使用房间 ID 作为 `target_id`。

### 6. 媒体上传

发送图片、视频、语音、文件等媒体类型时，适配器会自动处理上传：

- 上传接口：`POST /_matrix/media/v3/upload`
- 支持的输入格式：`bytes`、`mxc://` URI、HTTP URL、本地文件路径

### 7. 消息回复机制

Matrix 的消息回复通过 `m.relates_to` 字段实现：

- 发送消息时，如果设置了 `.Reply(event_id)`，会在消息内容中添加 `m.relates_to` 字段
- Matrix 协议会自动在客户端显示回复引用

### 8. 消息提及机制

Matrix 支持通过 `m.mentions` 字段进行用户提及：

- `.At(user_id)` 会在消息中添加 `m.mentions.user_ids` 字段
- `.AtAll()` 会在消息中添加 `m.mentions.room` 字段（对应 Matrix 的 `@room`）