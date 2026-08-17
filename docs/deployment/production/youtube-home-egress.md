# YouTube 家庭网络出口（临时个人方案）

这个方案只把 Notebook Agent 的 YouTube 元数据和字幕请求转到 Mac
的家庭网络。数据库、Redis、MinIO、Embedding、邮件、Web、MCP 和 Caddy
不会使用这个代理。

它是按需能力：Mac、前台助手或 SSH 隧道停止后，已配置代理的 YouTube
任务会失败关闭，不会改用生产服务器 IP 直连。

## 一次性准备

在 Mac 上显式安装 tinyproxy。助手脚本不会代为安装，也不会创建
LaunchAgent 或 Homebrew service：

```sh
brew install tinyproxy
```

确认 Mac 已经可以通过 SSH key 非交互登录生产服务器：

```sh
ssh -o BatchMode=yes ubuntu@51.79.159.110 true
```

## 每次开始使用

在仓库根目录运行：

```sh
./scripts/youtube-home-egress ubuntu@51.79.159.110 18080 18080
```

脚本会在启动长期进程前检查 Mac 和服务器的 `127.0.0.1:18080` 是否
空闲。它生成仅当前会话使用的私有 tinyproxy 配置，限制为 YouTube /
Googlevideo 目的域和 HTTPS CONNECT 443，然后建立服务器回环反向转发。

看到以下提示后才可以提交或重试一个视频：

```text
YouTube home egress is ready. Keep this terminal open; press Ctrl-C to stop.
```

首次启用时，在另一个终端确认生产服务器只有回环监听：

```sh
ssh ubuntu@51.79.159.110 "ss -ltn '( sport = :18080 )'"
```

输出的本地地址必须是 `127.0.0.1:18080`，不能是 `0.0.0.0`、`[::]` 或
任何公网地址。

## 生产 Worker 激活

这一步只在包含该功能的版本已经按不可变 release 流程部署、且前台助手
显示 ready 后执行。用 `sudoedit` 在
`/etc/notebook-agent/notebook-agent.env` 增加：

```text
YOUTUBE_PROXY_URL=http://127.0.0.1:18080
```

只重启 Worker：

```sh
sudo systemctl restart notebook-agent-worker
sudo systemctl is-active notebook-agent-worker
```

先用一个公开视频验证元数据和非空字幕，确认成功后再重试至多一个原失败
条目。遇到 429、403、机器人挑战、代理错误或任何意外日志内容时立即停止，
不要批量提交或轮换 IP。

## 停止与回滚

正常停止时，在 Mac 的助手终端按 `Ctrl-C`。脚本会停止 SSH 和 tinyproxy，
并删除临时配置。之后从生产环境文件移除 `YOUTUBE_PROXY_URL`，只重启
Worker：

```sh
sudoedit /etc/notebook-agent/notebook-agent.env
sudo systemctl restart notebook-agent-worker
```

回滚不修改数据库、对象存储、Redis、Caddy、Web/MCP 或其他服务。移除代理
后，生产 IP 仍可能被 YouTube 限流；回滚只恢复原路由，不保证字幕恢复。
