import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router";

import {
  ApiError,
  approveBrowserCompanionPairing,
  listBrowserCompanionDevices,
  revokeBrowserCompanionDevice,
} from "../api/client";

const PAIRING_ID_RE = /^[a-f0-9]{32}$/;

function companionError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "extension_pairing_expired") return "这次配对已过期，请回到插件重新开始。";
    if (error.code === "extension_pairing_used") return "这次配对已经完成，请在插件中继续。";
    if (error.code === "extension_pairing_invalid") return "配对请求无效，请从插件重新打开此页面。";
    if (error.code === "extension_device_not_found") return "该设备已经断开。";
  }
  return "暂时无法完成操作，请稍后重试。";
}

export function BrowserCompanionPage() {
  const [params] = useSearchParams();
  const pairingId = useMemo(() => params.get("pairing")?.trim() ?? "", [params]);
  const validPairing = PAIRING_ID_RE.test(pairingId);
  const queryClient = useQueryClient();
  const devices = useQuery({
    queryKey: ["browser-companion", "devices"],
    queryFn: listBrowserCompanionDevices,
  });
  const approve = useMutation({
    mutationFn: () => approveBrowserCompanionPairing(pairingId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["browser-companion", "devices"] }),
  });
  const revoke = useMutation({
    mutationFn: revokeBrowserCompanionDevice,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["browser-companion", "devices"] }),
  });

  return (
    <section className="account-link-page companion-page" aria-labelledby="companion-title">
      <header className="account-link-page__heading">
        <p className="eyebrow">可选获取方式</p>
        <h1 id="companion-title">浏览器伴侣</h1>
        <p>插件使用你浏览器里已经打开的页面读取字幕，适合 NTULearn，也能在 YouTube 服务器访问受限时使用。原有的 YouTube 保存方式不会被替换。</p>
      </header>

      {pairingId ? (
        <section className="account-link-card companion-pairing" aria-labelledby="companion-pair-title">
          <p className="step-number" aria-hidden="true">01</p>
          <h2 id="companion-pair-title">连接这个插件</h2>
          <p>连接后，插件只能向你的资料库提交当前视频的字幕和公开元数据，不能读取资料库、聊天或账户设置。</p>
          {!validPairing ? <p className="inline-error" role="alert">配对链接格式无效，请从插件重新开始。</p> : null}
          {approve.isSuccess ? <p className="account-link-status" role="status">已批准。请回到插件，它会自动完成连接。</p> : null}
          {approve.isError ? <p className="inline-error" role="alert">{companionError(approve.error)}</p> : null}
          <button
            className="button button--primary button--wide"
            type="button"
            disabled={!validPairing || approve.isPending || approve.isSuccess}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? "正在连接…" : approve.isSuccess ? "已连接" : "允许连接"}
          </button>
        </section>
      ) : (
        <section className="account-link-card companion-pairing" aria-labelledby="companion-install-title">
          <p className="step-number" aria-hidden="true">01</p>
          <h2 id="companion-install-title">按需安装和使用</h2>
          <p>从插件弹窗发起配对后，本页会显示确认按钮。你也可以随时在浏览器里禁用或卸载插件。</p>
        </section>
      )}

      <section className="account-link-card companion-devices" aria-labelledby="companion-devices-title">
        <p className="step-number" aria-hidden="true">02</p>
        <h2 id="companion-devices-title">已连接的插件</h2>
        {devices.isPending ? <p aria-live="polite">正在读取连接状态…</p> : null}
        {devices.isError ? <p className="inline-error" role="alert">{companionError(devices.error)}</p> : null}
        {devices.data?.devices.length === 0 ? <p>目前没有已连接的插件。</p> : null}
        {devices.data?.devices.map((device) => (
          <article className="companion-device" key={device.device_id}>
            <div>
              <strong>{device.client_label}</strong>
              <p>版本 {device.client_version}{device.revoked_at ? " · 已断开" : " · 已连接"}</p>
            </div>
            <button
              className="button button--quiet"
              type="button"
              disabled={Boolean(device.revoked_at) || revoke.isPending}
              onClick={() => revoke.mutate(device.device_id)}
            >
              {device.revoked_at ? "已断开" : "断开连接"}
            </button>
          </article>
        ))}
        {revoke.isError ? <p className="inline-error" role="alert">{companionError(revoke.error)}</p> : null}
      </section>

      <p className="account-link-warning" role="note">插件不会上传 NTU/YouTube 登录 Cookie、SAML、Kaltura 会话密钥、签名视频地址或音视频文件；没有字幕的视频会标记为“需要语音转写”。</p>
    </section>
  );
}
