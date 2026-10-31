[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<div align="center">

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="ErisPulse-MatrixAdapter" />

# ErisPulse-MatrixAdapter

**Адаптер ErisPulse для Matrix — децентрализованный чат, long-polling**

<p>
  <a href="https://pypi.org/project/ErisPulse-MatrixAdapter/"><img src="https://img.shields.io/pypi/v/ErisPulse-MatrixAdapter?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/ErisPulse-MatrixAdapter/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>
  <a href="https://github.com/ErisPulse/ErisPulse/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>
  <img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/adapter.svg" alt="ErisPulse">
</p>

</div>

---

<a id="english"></a>

## English

A Matrix protocol adapter built on the [ErisPulse](https://github.com/ErisPulse/ErisPulse/) framework. It receives events via the Long Polling Sync API and integrates modules for multiple scenarios such as private chats and groups, providing a unified interface for event handling and message operations.

### Usage Examples

#### OneBot12 Standard Event Types

The MatrixAdapter is fully compatible with the OneBot12 standard event format, with some extension fields:

| Event Type | detail_type | Description |
|------------|-------------|-------------|
| Message (Private) | private | A message sent by a user in a DM room |
| Message (Group) | group | A message sent by a user in a group room |
| Group Member Increase | group_member_increase | A user joined the room |
| Group Member Decrease | group_member_decrease | A user left or was banned |
| Member Info Update | matrix_member_update | Room member information changed |
| Matrix Reaction | matrix_reaction | A reaction to a message |
| Matrix Redaction | matrix_redaction | A message was redacted/deleted |
| Matrix Room Name Change | matrix_name | The room name changed |
| Matrix Room Topic Change | matrix_topic | The room topic changed |
| Matrix Room Avatar Change | matrix_avatar | The room avatar changed |
| Matrix Power Levels Change | matrix_power_levels | Room permissions changed |

---

### Sending Messages

```python
from ErisPulse import sdk
matrix = sdk.adapter.get("matrix")

# Send a text message
await matrix.Send.To("group", room_id).Text("Hello World!")

# Send a message with @mention
await matrix.Send.To("group", room_id).At("@user:matrix.org").Text("Hello")

# Send a message mentioning everyone
await matrix.Send.To("group", room_id).AtAll().Text("Announcement")

# Send a reply message
await matrix.Send.To("group", room_id).Reply("$event_id").Text("Reply content")

# Send an image (URL)
await matrix.Send.To("group", room_id).Image("https://example.com/image.png")

# Send an image (MXC URI)
await matrix.Send.To("group", room_id).Image("mxc://matrix.org/abc123")

# Send an image (binary data)
with open("image.png", "rb") as f:
    image_data = f.read()
await matrix.Send.To("group", room_id).Image(image_data)

# Send an image (local file path)
await matrix.Send.To("group", room_id).Image("/path/to/image.png")

# Send a notice message (m.notice)
await matrix.Send.To("group", room_id).Notice("System notice")

# Send an HTML-formatted message
await matrix.Send.To("group", room_id).Html("<b>bold</b> <i>italic</i>", fallback="bold italic")

# Send a file (with filename)
await matrix.Send.To("group", room_id).File("/path/to/file.pdf", filename="document.pdf")

# Combined usage: reply + mention
await matrix.Send.To("group", room_id).Reply("$event_id").At("@user:matrix.org").Text("Composite message")

# Send a OneBot12-format message using Raw_ob12
message = [
    {"type": "text", "data": {"text": "First line"}},
    {"type": "image", "data": {"file": "https://example.com/img.jpg"}},
    {"type": "text", "data": {"text": "Second line"}}
]
await matrix.Send.To("group", room_id).Raw_ob12(message)
```

---

### Configuration

A default configuration is generated automatically on first run. MatrixAdapter supports multi-account configuration.

```toml
# config.toml
# Account 1
[Matrix_Adapter.accounts.default]
homeserver = "https://matrix.org"          # Matrix server address (required)
access_token = "YOUR_ACCESS_TOKEN"          # Access token (one of access_token or user_id+password)
user_id = ""                                # Matrix user ID (e.g. @bot:matrix.org)
password = ""                               # Matrix user password
auto_accept_invites = true                  # Whether to auto-accept room invites (optional, default true)
enabled = true                              # Whether to enable (optional, default true)

# Account 2
[Matrix_Adapter.accounts.bot2]
homeserver = "https://matrix.example.com"
access_token = "ANOTHER_TOKEN"
enabled = true
```

> Backward compatibility: If an old single-account `[Matrix_Adapter]` configuration (containing access_token) is detected, it will be automatically migrated to `accounts.default`.

**Configuration fields (per account):**
- `homeserver`: Matrix server address (required), defaults to `https://matrix.org`
- `access_token`: Access token, obtainable from a Matrix client (e.g. Element) settings
- `user_id`: Matrix user ID (e.g. `@bot:matrix.org`), used together with `password`
- `password`: Matrix user password, used for auto-login to obtain an access_token
- `auto_accept_invites`: Whether to auto-accept room invites, defaults to `true`
- `enabled`: Whether to enable this account (optional, default true)

**Authentication methods:**
- Method 1 (recommended): Provide `access_token` directly
- Method 2: Provide `user_id` and `password`; the adapter will call the login API to obtain a token automatically

---

### End-to-End Encryption (E2EE)

The adapter natively supports end-to-end encryption for encrypted rooms (based on [matrix-nio](https://github.com/matrix-nio/matrix-nio), powered by [vodozemac](https://pypi.org/project/vodozemac/)): messages and media in encrypted rooms are automatically decrypted on receive and encrypted on send — no changes required in your modules.

```toml
[Matrix_Adapter.accounts.default]
homeserver = "https://matrix.org"
user_id = "@bot:matrix.org"
password = "YOUR_PASSWORD"
encryption_enabled = true       # Enable E2EE (default false)
trust_all_devices = true        # Auto-trust unverified devices (default true)
# device_id = "XXXXXXXXXX"      # Optional: specify manually only if the server doesn't report one
# store_path = "data/matrix/default"  # Optional: crypto session store directory
```

**Encryption fields (per account):**
- `encryption_enabled`: Enable E2EE, defaults to `false` (existing plaintext deployments are unaffected)
- `trust_all_devices`: Automatically trust unverified devices, defaults to `true` (common bot practice). Set to `false` for strict mode — unverified devices will not receive room keys for new messages
- `device_id`: Device ID, auto-resolved in most cases (login response → `/whoami` → persisted). Manual specification is only needed on servers that don't report it
- `store_path`: Crypto session store directory, defaults to `data/matrix/<account_name>`

**How it works:**
- The `device_id` is resolved on login and persisted (in `store_path/device.json`); password re-login reuses the same device to avoid device pile-up
- Sync runs through the E2EE session: device key upload, one-time key replenishment, to-device messages and Megolm decryption are handled automatically
- Sending to an encrypted room automatically shares the Megolm session and encrypts the payload; media uploads in encrypted rooms are encrypted end-to-end (`m.file.encrypted`)
- Modules can call `event.is_encrypted()` to check whether a message came from an encrypted room; encrypted media segments carry `matrix_encrypted_file` (JWK), and ciphertext can be decrypted with `MatrixAdapter.decrypt_media(bytes, key, iv, sha256)`

**Important notes:**
1. Requires Python >= 3.10 and `matrix-nio[e2e] >= 0.26` (installed automatically as a dependency)
2. Cross-signing and server-side key backups are not supported; historical messages cannot be decrypted if the local store directory (`store_path`) is deleted — do not delete it
3. Interactive SAS (emoji) verification is planned for a future release; the current trust model is "trust all devices" by default
4. Events that fail to decryption are skipped with a warning logged

---

### Matrix-Specific Features

See the [Matrix platform features documentation](platform-features.md) for platform-specific capabilities, including the decentralized architecture, room concepts, Long Polling sync, MXC URIs, HTML rich text, reactions, message editing, and extension field descriptions.

For the detailed event conversion reference, see the [conversion mapping documentation](CoverToOnebot12.md).

### Event Listening Examples

#### Using the Event Module (Recommended)

```python
from ErisPulse.Core.Event import message, notice

@message.on_message()
async def handle_message(event):
    if event["platform"] == "matrix":
        detail_type = event["detail_type"]
        if detail_type == "private":
            # Handle private message
            pass
        elif detail_type == "group":
            # Handle group message
            pass

@notice.on_notice()
async def handle_notice(event):
    if event["platform"] == "matrix":
        detail_type = event["detail_type"]
        if detail_type == "matrix_reaction":
            # Handle reaction
            reaction_key = event.get("matrix_reaction_key", "")
        elif detail_type == "matrix_redaction":
            # Handle message redaction
            redacted_id = event.get("matrix_redacted_event_id", "")
        elif detail_type == "group_member_increase":
            # Handle member join
            user_id = event.get("user_id", "")
```

#### Using OneBot12 Standard Events

```python
@sdk.adapter.on("message")
async def handle_message(event):
    if event["platform"] == "matrix":
        bot_id = event["self"]["user_id"]
        print(f"Message from Bot: {bot_id}")

@sdk.adapter.on("notice")
async def handle_notice(event):
    if event["platform"] == "matrix":
        # Handle Matrix notice event
        pass
```

#### Using Event Mixin Methods

```python
@message.on_message()
async def handle_message(event):
    if event.get("platform") != "matrix":
        return

    room_id = event.get_room_id()               # Get the room ID
    event_type = event.get_matrix_event_type()  # Get the raw Matrix event type
    sender = event.get_matrix_sender()          # Get the sender ID
    is_edited = event.is_edited()               # Whether the message is an edit
    is_notice = event.is_notice()               # Whether it is an m.notice type
    is_encrypted = event.is_encrypted()         # Whether it came from an E2EE room
```

### Notes

1. Make sure all handlers are registered before calling `startup()`.
2. Matrix is a decentralized protocol. User IDs use the format `@user:server.domain`, and room IDs use the format `!room_id:server.domain`.
3. Matrix does not distinguish between group chats and private chats — all conversations are "rooms". The adapter identifies private chats via DM account data automatically.
4. The adapter uses Long Polling (the `/sync` API) to receive events, not WebSocket.
5. Media files are referenced via `mxc://` URIs; the adapter supports automatic upload and download.
6. Call `shutdown()` on program exit to ensure resources are released.
7. Auto-accepting room invites is supported (can be disabled via the `auto_accept_invites` config).
8. HTML-formatted messages are supported via the `.Html()` method.
9. Message replies (`.Reply()`) and user mentions (`.At()`, `.AtAll()`) are supported.
10. With end-to-end encryption enabled, do not delete the `store_path` directory (see the "End-to-End Encryption" section).

---

### Reference Links

- [ErisPulse Main Repository](https://github.com/ErisPulse/ErisPulse/)
- [Matrix Protocol Specification](https://spec.matrix.org/)
- [Matrix Client-Server API](https://spec.matrix.org/v1.11/client-server-api/)
- [Module Development Guide](https://www.erisdev.com/#docs/developer-guide/README.md)

---

<a id="中文"></a>

## 中文

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

## 端到端加密（E2EE）

适配器原生支持加密房间的端到端加密（基于 [matrix-nio](https://github.com/matrix-nio/matrix-nio)，底层使用 [vodozemac](https://pypi.org/project/vodozemac/)）：加密房间的消息与媒体在接收时自动解密、发送时自动加密，模块无需任何改动。

```toml
[Matrix_Adapter.accounts.default]
homeserver = "https://matrix.org"
user_id = "@bot:matrix.org"
password = "YOUR_PASSWORD"
encryption_enabled = true       # 启用端到端加密（默认 false）
trust_all_devices = true        # 自动信任未验证设备（默认 true）
# device_id = "XXXXXXXXXX"      # 可选：仅在服务器不返回时手动指定
# store_path = "data/matrix/default"  # 可选：加密会话存储目录
```

**加密相关配置（每个账户）：**
- `encryption_enabled`：启用端到端加密，默认 `false`（存量明文部署不受影响）
- `trust_all_devices`：自动信任未验证设备，默认 `true`（bot 场景惯例）；设为 `false` 进入严格模式——未验证设备将无法解密新消息
- `device_id`：设备 ID，多数情况自动获取（登录响应 → `/whoami` → 本地持久化），仅服务器不返回时需手动指定
- `store_path`：加密会话存储目录，默认 `data/matrix/<账户名>`

**工作原理：**
- 登录时解析并持久化 `device_id`（`store_path/device.json`），密码重登会复用同一设备，避免设备堆积
- 同步走 E2EE 会话：设备密钥上传、一次性密钥补充、to-device 消息、Megolm 解密全部自动完成
- 向加密房间发送消息时自动分享 Megolm 会话并加密载荷；加密房间的媒体上传为端到端加密（`m.file.encrypted`）
- 模块可通过 `event.is_encrypted()` 判断消息是否来自加密房间；加密媒体段携带 `matrix_encrypted_file`（JWK），可配合 `MatrixAdapter.decrypt_media(bytes, key, iv, sha256)` 解密密文

**注意事项：**
1. 需要 Python >= 3.10 与 `matrix-nio[e2e] >= 0.26`（已作为依赖自动安装）
2. 不支持交叉签名与服务器端密钥备份；本地会话目录（`store_path`）删除后历史消息将无法解密，请勿删除
3. 交互式 SAS（emoji）验证计划在后续版本提供，当前默认信任策略为"自动信任所有设备"
4. 解密失败的事件会被跳过并记录警告日志

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
    is_encrypted = event.is_encrypted()     # 是否来自端到端加密房间
```

## 注意事项：

1. Matrix 是去中心化协议，用户ID格式为 `@user:server.domain`，房间ID格式为 `!room_id:server.domain`
2. Matrix 不区分群聊和私聊，所有会话都是"房间"。适配器通过 DM 账户数据自动识别私聊
3. 适配器使用 Long Polling（`/sync` API）获取事件，而非 WebSocket
4. 媒体文件通过 `mxc://` URI 引用，适配器支持自动上传和下载
5. 支持自动接受房间邀请（可通过 `auto_accept_invites` 配置关闭）
6. 启用端到端加密后，请勿删除 `store_path` 目录（详见「端到端加密」章节）

---

### 参考链接

- [ErisPulse 主库](https://github.com/ErisPulse/ErisPulse/)
- [Matrix 协议规范](https://spec.matrix.org/)
- [Matrix Client-Server API](https://spec.matrix.org/v1.11/client-server-api/)
- [模块开发指南](https://www.erisdev.com/#docs/developer-guide/README.md)
