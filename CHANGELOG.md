# 更新日志

所有版本更新遵循 [语义化版本控制](https://semver.org/lang/zh-CN/) 规范。

---

## [4.1.0] - 2026/07/08

### 修复

- 修复 `MatrixAccountConfig` 未声明 `bot_id` 字段导致启动报错的问题
  - `bot_id` 不再写入配置 dataclass，改为运行时从登录响应获取并存储于 `_account_runtime` 字典

### 变更

- 配置字段 metadata 迁移至新格式：`description` 支持 i18n，`webui` 键更名 `ui`
- 新增 `_schema_meta.group_labels` 分组显示名（Dashboard 分区标题）
- **全面国际化**：所有日志/错误消息通过 `i18n.t()` 输出，注册 zh-CN/en 双语翻译
