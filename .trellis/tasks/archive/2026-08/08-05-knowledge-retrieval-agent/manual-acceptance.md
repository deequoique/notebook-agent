# 人工验收清单：私有知识库 Agent 与 Telegram / 微信

本文件从“自动实现已经完成”处开始。验收人负责真实模型、Telegram bot、两个
Telegram 测试账号，以及微信个人号扫码。不要把 token、二维码、私聊正文或
绑定码写入仓库和验收记录。

首次安装、服务启动、LangBot plugin 部署、systemd、备份、升级回滚和排障先按
项目根目录的 `docs/deployment.md` 执行；本文只负责真实平台验收。

## 0. 自动预检

```bash
alembic upgrade head
pytest -q
python -m app.cli --help
python -m app.cli ask --help
python -m app.cli users --help
```

预期：全部测试通过；CLI 的 ingest/search/ask 都不能省略 `--user-id`。

## 1. 隐私与固定版本

- [x] 确认 LangBot 为 4.10.6，plugin SDK 为 0.4.13。
- [x] 对 LangBot 源码先 `patch --dry-run -p1`、再应用
      `integrations/langbot-4.10.6-redact-monitoring.patch`。
- [x] 安装并启用 `integrations/langbot_kb_plugin/`。
- [x] 应用和插件使用同一个高熵 `CHANNEL_GATEWAY_SECRET`，且文件未被提交。
- [x] `KB_BOT_CHANNELS` 明确包含 Telegram 与微信两个 bot UUID，没有默认渠道。
- [x] `plugin.required_plugins` 明确包含
      `notebook-agent/notebook-knowledge-agent`；bridge pipeline 关闭“启用全部插件”并显式绑定它。
- [x] LangBot 日志出现 `Required plugins initialized; message adapters may start.` 后，两个
      adapter 才开始运行；不得使用固定等待时间或 Local Agent fallback。
- [x] 启动后检查 LangBot 日志和 monitoring：看不到私聊正文、昵称、sender ID；
      monitoring 内容应是固定 `[redacted by notebook-agent deployment]`。

以上固定版本、启动顺序与隐私证据由已完成的
`08-06-fix-langbot-startup-race` 和 `08-06-diagnose-wechat-whoami` 子任务提供；
不代表 Telegram 端到端、跨渠道知识库或禁用/恢复项目已经验收。

LangBot Telegram 配置参考：https://docs.langbot.app/en/usage/platforms/telegram

LangBot OpenClaw/iLink 微信配置参考：
https://docs.langbot.app/en/usage/platforms/wechat/weixin

## 2. 启动顺序

1. 启动 PostgreSQL、Redis、MinIO，并执行 migration。
2. 配置真实 embedding 和 Agent provider。
3. 启动 `python -m app.cli gateway-server`，确认 `GET /health` 返回 `ok`。
4. Docker/WebSocket 模式先启动 plugin runtime；stdio 模式由 LangBot core 启动它，再启动
   LangBot core 并等待 required bridge 为 `initialized`。
5. 同时启用 Telegram 与微信 adapter，不做二选一配置；patched core 会在 readiness 通过后
   自动启动所有 enabled adapter。

## 3. 准备两份互斥知识库

1. Telegram 账号 A 发送 `/start`，记录回复中的内部用户编号 A。
2. Telegram 账号 B 发送 `/start`，记录内部用户编号 B；两者必须不同。
3. 向 A 导入只含“量子菠萝”测试概念的视频，向 B 导入只含“月球草莓”的视频：

```bash
python -m app.cli ingest --user-id <A> '<A的视频URL>'
python -m app.cli ingest --user-id <B> '<B的视频URL>'
```

- [ ] A、B 重复 `/start` 或 `/whoami` 始终得到各自相同编号。
- [ ] 数据库中每个外部身份只对应一个 `ChannelIdentity`。

## 4. Telegram 完整端到端

- [ ] A 询问自己的独有概念，答案含标题、原文证据和可点击时间戳。
- [ ] 点击时间戳，视频位置与关键结论一致。
- [ ] A 询问 B 的独有概念，明确返回知识库中未找到。
- [ ] B 询问 A 的独有概念，同样不能命中。
- [ ] 提一个需要 `search_segments → get_neighbors/get_item → open_at` 的问题；答案正确。
- [ ] 接着发送“那它后面还说了什么？”，能利用前一轮上下文回答。
- [ ] 重启 gateway-server 与 LangBot runtime，再发送一个依赖前文的追问，仍能恢复。
- [ ] 发送 `/new` 后再发同样追问，系统不再使用旧上下文。
- [ ] 重发同一平台消息（若客户端允许）不会创建重复 turn。
- [ ] 临时配置无效模型 key/停止 retrieval 后，系统在限制内给出可理解失败，不生成无来源答案。

## 5. 自助跨渠道绑定

1. A 在 Telegram 发送 `/link wechat`。
2. 微信个人号私聊 bot，发送 `/link <绑定码>`。
3. 微信发送 `/whoami`，必须得到内部用户编号 A。

- [ ] 微信能查询 A 的私有内容，但不能查询 B 的私有内容。
- [ ] 再次使用同一个绑定码被拒绝。
- [ ] 等待绑定码过期后使用被拒绝。
- [ ] 把限定微信的绑定码发到其他渠道被拒绝。
- [ ] 不使用绑定码的新微信身份会得到新用户，不按昵称自动合并。

## 6. 微信个人号私聊 smoke 与并发网关

- [x] OpenClaw/iLink 扫码登录成功，私聊入站能收到并回复。
- [ ] Telegram 与微信保持同时启用，交错发送消息，回复回到正确来源。
- [ ] 同一用户在 Telegram 与微信的知识库相同，但两边对话上下文默认不混合。
- [ ] 人为停止微信 adapter 后，Telegram 仍能继续完成问答。
- [ ] 恢复微信 adapter 后，微信可再次问答，不需要重建 AppUser。
- [ ] 检查 fallback `compat-...` message ID 是否被使用，并在结果中记录；这是
      LangBot 4.10.6 未稳定序列化平台 message ID 的已知限制。

微信基础 smoke 已在 `08-06-diagnose-wechat-whoami` 中验证：同一身份重复和安全重启后
`/whoami` 均返回相同内部编号，重复微信身份键为 `0`。本节其余并发、知识库和 adapter
隔离项目仍需在本父任务中人工执行。

## 7. 禁用与恢复

```bash
python -m app.cli users disable --user-id <A>
python -m app.cli users enable --user-id <A>
```

- [ ] 禁用后 Telegram 和微信都在调用 embedding/模型/retrieval 前拒绝。
- [ ] 恢复后两个渠道重新可用，知识库仍属于原用户。

## 8. 验收结论

只有以上项目全部通过，才把 Trellis 任务标记完成并归档。Slack 不属于本次实机
验收；它的通过条件是新增 adapter 配置时不改变 `ChannelEnvelope`、身份解析、
Agent 工具或 tenant-scoped retrieval contract。
