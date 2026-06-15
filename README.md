# MatrixAdapter 模块文档

## 简介
MatrixAdapter 是基于 [ErisPulse](https://github.com/ErisPulse/ErisPulse/) 架构的 Matrix 协议适配器，通过 Long Polling Sync API 接收事件，整合了私聊、群组等多种场景的功能模块，提供统一的事件处理和消息操作接口。

## 使用示例

### OneBot12标准事件类型

MatrixAdapter 适配器完全兼容 OneBot12 标准事件格式，并提供了一些扩展字段：

| 事件类型 | detail_type | 说明 |
|----------|-------------|------|
| 消息事件（私聊） | private | DM房间中用户发送的消息 |
| 消息事件（群组） | group | 群组房间中用户发送的消息 |
| 群成员增加 | group_member_increase | 用户加入房间 |
| 群成员减少 | group_member_decrease | 用户离开/被封禁 |
| 成员信息更新 | matrix_member_update | 房间成员信息变更 |
| Matrix表情回应 | matrix_reaction | 消息表情回应 |
| Matrix消息撤回 | matrix_redaction | 消息被撤回/删除 |
| Matrix房间名称变更 | matrix_name | 房间名称变更 |
| Matrix房间话题变更 | matrix_topic | 房间话题变更 |
| Matrix房间头像变更 | matrix_avatar | 房间头像变更 |
| Matrix权限等级变更 | matrix_power_levels | 房间权限变更 |

---

## 消息发送示例

```python
from ErisPulse import sdk
matrix = sdk.adapter.get("matrix")

# 发送文本消息
await matrix.Send.To("group", room_id).Text("Hello World!")

# 发送带@的消息
await matrix.Send.To("group", room_id).At("@user:matrix.org").Text("你好")

# 发送带@所有人的消息
await matrix.Send.To("group", room_id).AtAll().Text("公告通知")

# 发送回复消息
await matrix.Send.To("group", room_id).Reply("$event_id").Text("回复内容")

# 发送图片（URL）
await matrix.Send.To("group", room_id).Image("https://example.com/image.png")

# 发送图片（MXC URI）
await matrix.Send.To("group", room_id).Image("mxc://matrix.org/abc123")

# 发送图片（二进制数据）
with open("image.png", "rb") as f:
    image_data = f.read()
await matrix.Send.To("group", room_id).Image(image_data)

# 发送图片（本地文件路径）
await matrix.Send.To("group", room_id).Image("/path/to/image.png")

# 发送通知消息（m.notice）
await matrix.Send.To("group", room_id).Notice("系统通知")

# 发送HTML格式消息
await matrix.Send.To("group", room_id).Html("<b>加粗</b> <i>斜体</i>", fallback="加粗 斜体")

# 发送文件（带文件名）
await matrix.Send.To("group", room_id).File("/path/to/file.pdf", filename="文档.pdf")

# 组合使用：回复 + @
await matrix.Send.To("group", room_id).Reply("$event_id").At("@user:matrix.org").Text("复合消息")

# 使用 Raw_ob12 发送 OneBot12 格式消息
message = [
    {"type": "text", "data": {"text": "第一行"}},
    {"type": "image", "data": {"file": "https://example.com/img.jpg"}},
    {"type": "text", "data": {"text": "第二行"}}
]
await matrix.Send.To("group", room_id).Raw_ob12(message)
```

---

### 配置说明

首次运行会自动生成默认配置。MatrixAdapter 支持多账户配置。

```toml
# config.toml
# 账户1
[Matrix_Adapter.accounts.default]
homeserver = "https://matrix.org"          # Matrix服务器地址（必填）
access_token = "YOUR_ACCESS_TOKEN"          # 访问令牌（与 user_id+password 二选一）
user_id = ""                                # Matrix用户ID（如 @bot:matrix.org）
password = ""                               # Matrix用户密码
auto_accept_invites = true                  # 是否自动接受房间邀请（可选，默认为true）
enabled = true                              # 是否启用（可选，默认为true）

# 账户2
[Matrix_Adapter.accounts.bot2]
homeserver = "https://matrix.example.com"
access_token = "ANOTHER_TOKEN"
enabled = true
```

> 兼容旧配置：若检测到旧的单账户 `[Matrix_Adapter]` 配置（含 access_token），会自动迁移为 `accounts.default`。

**配置项说明（每个账户）：**
- `homeserver`：Matrix服务器地址（必填），默认为 `https://matrix.org`
- `access_token`：访问令牌，可从Matrix客户端（如 Element）的设置中获取
- `user_id`：Matrix用户ID（如 `@bot:matrix.org`），与 `password` 配合使用
- `password`：Matrix用户密码，用于自动登录获取 access_token
- `auto_accept_invites`：是否自动接受房间邀请，默认为 `true`
- `enabled`：是否启用该账户（可选，默认为true）

**认证方式：**
- 方式一（推荐）：直接提供 `access_token`
- 方式二：提供 `user_id` 和 `password`，适配器会自动调用登录接口获取 token

---

## Matrix平台特有功能

请参考 [Matrix平台特性文档](platform-features.md) 了解Matrix平台的特有功能，包括去中心化架构、房间概念、Long Polling同步、MXC URI、HTML富文本、表情回应、消息编辑、扩展字段说明等内容。

详细的事件转换对照请参考 [转换对照文档](CoverToOnebot12.md)。

## 事件监听示例

### 使用 Event 模块（推荐）

```python
from ErisPulse.Core.Event import message, notice

@message.on_message()
async def handle_message(event):
    if event["platform"] == "matrix":
        detail_type = event["detail_type"]
        if detail_type == "private":
            # 处理私聊消息
            pass
        elif detail_type == "group":
            # 处理群组消息
            pass

@notice.on_notice()
async def handle_notice(event):
    if event["platform"] == "matrix":
        detail_type = event["detail_type"]
        if detail_type == "matrix_reaction":
            # 处理表情回应
            reaction_key = event.get("matrix_reaction_key", "")
        elif detail_type == "matrix_redaction":
            # 处理消息撤回
            redacted_id = event.get("matrix_redacted_event_id", "")
        elif detail_type == "group_member_increase":
            # 处理成员加入
            user_id = event.get("user_id", "")
```

### 使用 OneBot12 标准事件

```python
@sdk.adapter.on("message")
async def handle_message(event):
    if event["platform"] == "matrix":
        bot_id = event["self"]["user_id"]
        print(f"消息来自Bot: {bot_id}")

@sdk.adapter.on("notice")
async def handle_notice(event):
    if event["platform"] == "matrix":
        # 处理Matrix通知事件
        pass
```

### 使用 Event Mixin 方法

```python
@message.on_message()
async def handle_message(event):
    if event.get("platform") != "matrix":
        return

    room_id = event.get_room_id()           # 获取房间ID
    event_type = event.get_matrix_event_type()  # 获取原始Matrix事件类型
    sender = event.get_matrix_sender()      # 获取发送者ID
    is_edited = event.is_edited()           # 是否为编辑消息
    is_notice = event.is_notice()           # 是否为 m.notice 类型
```

## 注意事项：

1. 确保在调用 `startup()` 前完成所有处理器的注册
2. Matrix 是去中心化协议，用户ID格式为 `@user:server.domain`，房间ID格式为 `!room_id:server.domain`
3. Matrix 不区分群聊和私聊，所有会话都是"房间"。适配器通过 DM 账户数据自动识别私聊
4. 适配器使用 Long Polling（`/sync` API）获取事件，而非 WebSocket
5. 媒体文件通过 `mxc://` URI 引用，适配器支持自动上传和下载
6. 程序退出时请调用 `shutdown()` 确保资源释放
7. 支持自动接受房间邀请（可通过 `auto_accept_invites` 配置关闭）
8. 支持发送 HTML 格式消息，使用 `.Html()` 方法
9. 支持消息回复（`.Reply()`）和用户提及（`.At()`、`.AtAll()`）

---

### 参考链接

- [ErisPulse 主库](https://github.com/ErisPulse/ErisPulse/)
- [Matrix 协议规范](https://spec.matrix.org/)
- [Matrix Client-Server API](https://spec.matrix.org/v1.11/client-server-api/)
- [模块开发指南](https://www.erisdev.com/#docs/developer-guide/README.md)