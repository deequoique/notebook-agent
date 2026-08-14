# Notebook Agent 浏览器伴侣（试点）

这是一个可选安装的 Manifest V3 Chrome/Chromium 插件。安装、配对后，它可以从用户当前打开的 YouTube 或 NTULearn/Kaltura 页面读取字幕并提交到 Notebook Agent；原有服务器端 YouTube 获取方式保持不变。

## 构建与加载

在 `extension/` 中使用项目要求的 Node 版本运行：

```console
pnpm install
pnpm test
pnpm build
```

然后在 `chrome://extensions` 开启“开发者模式”，选择“加载已解压的扩展程序”，加载 `extension/dist/`。

试点 manifest 使用固定公开 key，扩展 ID 是 `omogodipchfidpikpeebgmlplpkjnpfm`。后端必须精确配置：

```dotenv
BROWSER_COMPANION_ALLOWED_ORIGINS=chrome-extension://omogodipchfidpikpeebgmlplpkjnpfm
```

插件当前只连接 `https://notebookai.deequoique.tech`。换部署地址时，必须同时修改 `src/worker.ts` 的 `API_ORIGIN`、manifest 的精确 host permission 和后端允许的固定扩展 origin，再重新构建与审核。

## 隐私边界

- 页面中的 Cookie、SAML、Kaltura KS、签名字幕/视频 URL 只在当前页面获取字幕时临时使用，不进入提交数据。
- 插件只提交规范化字幕 cue、平台视频 ID、无秘密的规范 URL 和公开元数据。
- 无字幕时只提交 `unavailable`，不上传音频或视频。
- 捕获 Bearer 只可使用浏览器捕获与自助撤销接口，不能读取资料库、聊天或 Web/MCP API。
- 弹窗“断开连接”会先请求后端撤销该 Bearer，再清除本地凭据；网页账户页也可以撤销设备。
