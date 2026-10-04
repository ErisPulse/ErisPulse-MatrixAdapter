# 更新日志

所有版本更新遵循 [语义化版本控制](https://semver.org/lang/zh-CN/) 规范。

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
