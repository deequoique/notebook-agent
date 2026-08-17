# 微信渠道接入选型

调研日期：2026-08-05

## 共同架构

无论选择哪一种微信渠道，都只新增 `WeChatChannelAdapter`：

```text
微信渠道事件 → adapter → AgentRequest → PydanticAI Agent
                                     ↓
微信渠道消息 ← renderer ← AgentAnswer
```

Agent、检索工具、provider/gateway registry 和答案证据结构不因渠道变化。

## 方案 A：企业微信自建应用

**适合**：本人、团队或企业内部使用；希望最快获得官方、稳定的双向消息能力。

- 企业微信把成员发给应用的消息推送到 HTTPS callback。
- 服务端校验签名并解密消息，再被动回复或调用应用消息接口主动发送。
- 需要企业微信组织、管理员配置、公开 HTTPS URL、Token 和 EncodingAESKey。
- 主要面向企业成员，不等同于个人微信好友机器人。

**评价**：首个微信接入的推荐方案，接口边界清楚，适合验证 Agent channel adapter。

调研入口：

- [企业微信：接收消息与事件](https://developer.work.weixin.qq.com/document/path/90238)
- [企业微信：发送应用消息](https://developer.work.weixin.qq.com/document/path/90236)

## 方案 B：微信公众号

**适合**：让普通微信用户关注公众号后，通过公众号对话检索知识库。

- 用户给公众号发消息，微信服务器回调开发者服务器。
- 可以使用被动回复；主动客服消息受平台会话窗口、帐号类型和接口权限约束。
- 需要公众号、服务器配置和相应接口权限；部分能力可能要求认证。
- 用户体验是“和公众号聊天”，不是个人好友聊天。

**评价**：适合未来对外开放，但帐号和消息规则比企业微信自建应用复杂。

调研入口：

- [微信公众号：接收普通消息](https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Receiving_standard_messages.html)
- [微信公众号：被动回复用户消息](https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Passive_user_reply_message.html)
- [微信公众号：客服消息](https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Service_Center_messages.html)

## 方案 C：微信客服（企业微信能力）

**适合**：让外部普通微信用户从客服入口、二维码或链接进入会话，定位为公开服务/客服机器人。

- 企业通过微信客服 API 同步客户消息，并在会话允许的范围内回复。
- 面向外部微信用户，比企业微信内部应用覆盖更广。
- 需要企业微信主体、客服帐号、会话状态同步、消息去重和更完整的运营配置。
- 交互语义偏客服，会带来人工接管、会话分配等额外产品问题。

**评价**：适合对外服务，不建议作为最早的技术验证渠道。

调研入口：

- [企业微信开发文档](https://developer.work.weixin.qq.com/document/)
- 站内关键词：`微信客服 接收消息 读取消息 发送消息`

## 方案 D：微信小程序

**适合**：需要富 UI、搜索结果卡片、视频封面、多个时间戳、筛选器和流式答案。

- 小程序作为前端，通过项目自己的 HTTP/WebSocket/SSE API 调 Agent。
- 不受公众号纯消息格式限制，最适合展示知识库引用和跳转卡片。
- 用户必须打开小程序，不是微信聊天列表里的原生机器人。
- 需要小程序帐号、前端开发、域名配置、审核和发布流程。

**评价**：产品体验最好，但开发量最大，适合 Web Agent 稳定后再做。

调研入口：[微信小程序开发指南](https://developers.weixin.qq.com/miniprogram/dev/framework/)

## 方案 E：个人微信桌面端 Hook / UI 自动化

**适合**：仅做不可依赖的个人实验。

- 通常依赖非官方协议、客户端 Hook 或桌面 UI 自动化。
- 协议升级即可能失效，并存在帐号限制或封禁风险。
- 很难做到可靠回调、幂等、消息状态和部署运维。

**评价**：不进入产品路线，也不作为 P1/P2 验收方案。

## 推荐顺序

1. 企业微信自建应用：最快验证官方双向 channel adapter。
2. 微信公众号：需要面向普通微信用户时接入。
3. 微信小程序：需要更好的知识库展示体验时接入。
4. 微信客服：产品明确走外部服务/客服场景时接入。
5. 个人微信自动化：不采用。

