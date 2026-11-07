# 更新日志

所有版本更新遵循 [语义化版本控制](https://semver.org/lang/zh-CN/) 规范。

---

## [4.6.0] - 2026/11/06

### 新增（OneBot12 协议映射对齐）

- **标准私聊发送路径**：`Send.To("user", "@user:server")` 现在自动反查 DM 房间表得到私聊房间；
  `!room:server` 房间 ID 直接使用；无法定位房间时返回标准错误 `retcode=10003`（此前用户 ID 被直接当
  房间 ID 使用，请求必然 404）
- **标准 API 动作补齐**：新增 `set_group_name`（PUT m.room.name）、`get_group_member_info`
  （GET m.room.member）；`get_friend_list` 明确返回 `retcode=10002`（Matrix 无好友概念）
- **位置消息双向支持**：接收侧从 geo URI 解析出 latitude/longitude（此前恒为 0.0）；发送侧支持标准
  `location` 段（转 m.location 事件，与文本共存时按序拆分为独立事件）
- 接收侧解析 `m.mentions`（Matrix v1.11+）为标准 `mention` 段，`@room` 映射 `user_id="all"`——
  模块的 `must_at_bot` / `has_mention` 判定现在对 Matrix 生效

### 修复

- **接收侧音频段名纠正**：`m.audio` 产出段名由非标准的 `voice` 改为 OB12 标准的 `audio`（发送侧不受影响）
- **API 响应 retcode 恒为 int**：Matrix 的 `errcode` 是字符串（如 `M_FORBIDDEN`），此前失败时被直接
  填入 retcode 违反 OneBot12 返回码规范；现恒降级为 `34000`，原始 errcode 保留在 `message` 与 `matrix_raw`
- 群组 API 动作参数名由 `room_id` 更正为规范的 `group_id`（`get_group_info` / `get_group_member_list` /
  `leave_group` 等；关键字调用请改用 `group_id=`）
- `get_group_member_list` 元素补齐 `user_name` / `user_displayname` 字段（此前仅有 `user_id`），
  并过滤已退出/被封禁成员；`get_user_info` / `get_self_info` 补 `user_displayname`
- matrix-nio 未安装时适配器导入崩溃（`NioSession` 类型注解在类定义期求值）——E2EE 缺失时
  应回退明文模式而非抛 NameError

### 兼容性说明

- 依赖接收 `voice` 段名的模块请改判 `audio`（标准段名，与 Telegram 等适配器一致）
- 以关键字调用群组 API（如 `get_group_info(room_id=...)`）请改为 `group_id=...`

---

## [4.3.0] - 2026/10/04

### 新增

- **端到端加密（E2EE）原生支持**（基于 matrix-nio[e2e] 0.26+ / vodozemac）
  - 加密房间消息接收自动解密（Megolm），发送自动加密（含 Megolm 会话分享）
  - 加密房间媒体上传/接收为端到端加密（`m.file.encrypted`），提供 `MatrixAdapter.decrypt_media()` 解密辅助
  - 设备密钥上传、一次性密钥补充、to-device 消息处理全自动
  - `device_id` 自动解析与持久化（登录响应 → `/whoami` → 本地 `store_path/device.json`），密码重登复用同一设备
  - 新增配置项：`encryption_enabled`（默认关闭）、`trust_all_devices`（默认自动信任）、`device_id`、`store_path`
  - 事件 Mixin 新增 `is_encrypted()`，媒体段新增 `matrix_encrypted_file`（JWK）与 `encrypted` 标记
  - E2EE 未启用或 matrix-nio 缺失时自动降级明文模式，存量部署零影响

### 变更

- 新增运行时依赖 `matrix-nio[e2e]>=0.26.0`（含 vodozemac 预编译 wheel，全平台含 Windows 无需编译 libolm）
- 补齐缺失的 LICENSE 文件与 `[build-system]` 构建配置

### 限制说明

- 不支持交叉签名与服务器端密钥备份；本地会话存储（`store_path`）删除后历史消息无法解密
- 交互式 SAS（emoji）验证暂未支持，计划后续版本提供

---

## [4.1.0] - 2026/07/08

### 修复

- 修复 `MatrixAccountConfig` 未声明 `bot_id` 字段导致启动报错的问题
  - `bot_id` 不再写入配置 dataclass，改为运行时从登录响应获取并存储于 `_account_runtime` 字典

### 变更

- 配置字段 metadata 迁移至新格式：`description` 支持 i18n，`webui` 键更名 `ui`
- 新增 `_schema_meta.group_labels` 分组显示名（Dashboard 分区标题）
- **全面国际化**：所有日志/错误消息通过 `i18n.t()` 输出，注册 zh-CN/en 双语翻译
