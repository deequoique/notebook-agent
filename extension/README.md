# Notebook Agent 浏览器伴侣（试点）

这是一个可选安装的 Manifest V3 Chrome/Chromium 插件。安装、配对后，它可以从用户当前打开的 YouTube 或 NTULearn/Kaltura 页面读取字幕并提交到 Notebook Agent；原有服务器端 YouTube 获取方式保持不变。

## 构建与加载

在 `extension/` 中使用项目要求的 Node 版本运行：

```console
pnpm install
pnpm test:coverage
pnpm typecheck
pnpm lint
pnpm build
pnpm run audit
```

然后在 `chrome://extensions` 开启“开发者模式”，选择“加载已解压的扩展程序”，加载 `extension/dist/`。

本地开发服务器运行在精确回环地址时，改用：

```console
pnpm package:local
```

本地构建的 API manifest 只允许 `http://127.0.0.1:8000/*`，生产构建只允许 `https://notebookai.deequoique.tech/*`；两者不会同时出现在同一个产物中。扩展 API 直接走回环 HTTP，避开本地自签名证书；配对批准页面仍由 `https://localhost:8443` 提供，以保留安全 Cookie。切换构建后必须在 `chrome://extensions` 重新加载 unpacked extension。
配对凭据会绑定到构建选择的 API origin；从生产版切换到本地版时会自动清理生产环境的待配对状态和 token，避免跨环境发送旧凭据。

试点 manifest 使用固定公开 key，扩展 ID 是 `omogodipchfidpikpeebgmlplpkjnpfm`。后端必须精确配置：

```dotenv
BROWSER_COMPANION_ALLOWED_ORIGINS=chrome-extension://omogodipchfidpikpeebgmlplpkjnpfm
```

插件从构建产物的精确 API host permission 解析唯一服务地址。默认生产构建连接 `https://notebookai.deequoique.tech`，本地构建连接 `http://127.0.0.1:8000`；请求有 10 秒超时，不会因服务不可达而无限等待。后端仍必须允许该构建对应的固定扩展 origin。

NTULearn 的视频播放器可能位于 Kaltura 官方跨域 frame 中，因此 manifest 还包含精确的 `https://cdnapisec.kaltura.com/*` 播放器权限。这个权限只用于在用户点击插件时进入播放器并在浏览器本地读取已授权字幕；播放器的 Cookie、KS、Authorization 和签名资源 URL 都不会进入 Notebook Agent 请求。

实际授权媒体页 `https://ntulearnv1.ntu.edu.sg/*` 也作为单独的精确 host permission 支持；不会扩大为 `*.ntu.edu.sg` 通配权限。该页面的 `page_url` 只保留 HTTPS origin/path，canonical Kaltura reference 仍使用既有的无秘密 NTULearnVideo 媒体链接。

YouTube 字幕优先读取 JSON3；若同一可信签名字幕端点返回空或非 JSON 内容，会依次尝试 WebVTT 和原始 YouTube XML。若三种 timed-text 表示都为空，最后使用当前页面公开的官方 `youtubei/v1/get_transcript` 端点；其不透明参数必须在解码后包含当前视频 ID，避免 SPA 导航后使用旧转录。所有回退仍在当前页面内完成，字幕 URL、转录参数和签名查询不会提交给 Notebook Agent。

## 隐私边界

- 页面中的 Cookie、SAML、Kaltura KS、签名字幕/视频 URL 只在当前页面获取字幕时临时使用，不进入提交数据。
- 插件只提交规范化字幕 cue、平台视频 ID、无秘密的规范 URL 和公开元数据。
- 无字幕时只提交 `unavailable`，不上传音频或视频。
- 捕获 Bearer 只可使用浏览器捕获与自助撤销接口，不能读取资料库、聊天或 Web/MCP API。
- 弹窗“断开连接”会先请求后端撤销该 Bearer，再清除本地凭据；网页账户页也可以撤销设备。
