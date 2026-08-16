# 实施计划

## Dependency gate

- [ ] 确认 `08-06-agent-retrieval-live-path` 已完成实现和主模型审查，再启动本任务。
- [ ] 重新生成前保存当前版本化 patch 与部署脚本 diff；不得覆盖用户的无关改动。

## 1. 复现与失败测试

- [ ] 记录当前生产实例已成功登录并持续 poll；默认路径以此为兼容基线，不强制 certifi 或 TLS preflight。
- [ ] 增加测试证明无效**显式** CA 会触发 `certificate_verification_failed`，且 adapter 不会报告 healthy。
- [ ] 增加状态测试证明 background poll task 创建不等于 healthy。

## 2. 实现可信 CA 初始化

- [ ] 在版本化 patch 中仅实现显式 CA override 的可读性校验和 client-local context。
- [ ] 确保默认路径不写全局 CA 环境、不强制 certifi、也不发额外 preflight GET。
- [ ] 确保 aiohttp/OpenClaw client 使用 verified context；不得关闭证书或 hostname verification。
- [ ] 将证书错误映射为稳定且脱敏的配置失败，避免无限 traceback/retry 同时报告 running。

## 3. 实现 adapter readiness

- [ ] 增加最小 adapter state machine；login/poll 成功后才 healthy。
- [ ] 暂时网络异常转 degraded/retrying，成功恢复后回 healthy；永久 TLS 配置错误转 failed。
- [ ] 通过现有 health/管理接口的兼容扩展暴露 state、safe code、last-success/retry metadata。
- [ ] 保留 required-plugin 启动门禁、Telegram 和现有 login/message behavior。

## 4. 自动验证

- [ ] 无效/不可读 CA fixture：明确失败且绝不 healthy。
- [ ] fake poll：连续成功、暂时失败后恢复、证书失败三种状态转换。
- [ ] fixed LangBot 4.10.6 patch dry-run/application、修改文件 Python compile。
- [ ] required plugin、Telegram、bridge 和现有 LangBot patch 回归。
- [ ] 全量相关测试与 `git diff --check`。

建议命令（实现者按实际文件补充）：

```bash
LANGBOT_4_10_6_WHEEL=/path/to/verified/langbot-4.10.6-py3-none-any.whl \
  .venv/bin/pytest -q tests/test_langbot_startup_patch.py
.venv/bin/pytest -q tests/test_langbot_bridge_plugin.py
.venv/bin/pytest -q
git diff --check
python3 ./.trellis/scripts/task.py validate 08-06-openclaw-tls-readiness
```

## 5. 部署验证与文档

- [ ] 更新部署文档：当前成功 poll 基线、可选 CA override、process vs adapter readiness、诊断、重启和 rollback。
- [ ] 重新生成 patched runtime 并重启 LangBot，不重置登录/绑定数据。
- [ ] 证明无 `CERTIFICATE_VERIFY_FAILED` 且连续 3 次 poll 成功或 healthy 2 分钟。
- [ ] Telegram/required plugin 自动回归通过；微信私聊最终 smoke 交给用户人工执行。

## Rollback points

- patch 无法干净应用：停止部署并修复版本化 patch，不手改 runtime 充当最终修复。
- readiness API 兼容失败：回滚展示扩展，保留内部状态和安全日志，再做兼容适配。
- TLS 仍失败：保持 adapter failed，禁止使用 insecure SSL 临时绕过。
