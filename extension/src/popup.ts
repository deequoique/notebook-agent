type Reply = { ok: boolean; result?: Record<string, unknown>; error?: string };

const statusNode = document.querySelector<HTMLParagraphElement>("#status")!;
const primary = document.querySelector<HTMLButtonElement>("#primary")!;
const disconnect = document.querySelector<HTMLButtonElement>("#disconnect")!;
let action: "pair" | "finish" | "capture" = "pair";

const messages: Record<string, string> = {
  unsupported_page: "请打开 YouTube 或 NTULearn/Kaltura 视频页面后再试。",
  pairing_pending: "网页端尚未批准，请完成批准后重试。",
  pairing_expired: "配对已过期，请重新连接。",
  caption_fetch_failed: "页面字幕暂时读取失败，请刷新视频页面后重试。",
  kaltura_entry_missing: "没有识别到 Kaltura 视频，请确认视频已加载后刷新页面重试。",
  capture_unavailable: "页面结构可能已经变化，请刷新视频页面或升级插件后重试。",
  extension_grant_revoked: "这个连接已经被撤销，请重新连接。",
};

async function send(type: string): Promise<Reply> {
  return chrome.runtime.sendMessage({ type }) as Promise<Reply>;
}

function render(reply: Reply) {
  const paired = reply.ok && reply.result?.paired === true;
  const pairing = reply.ok && reply.result?.pairing === true;
  action = paired ? "capture" : pairing ? "finish" : "pair";
  statusNode.textContent = paired ? "已连接。打开一个视频，然后保存当前页面的字幕。" : pairing ? "请先在刚打开的 Notebook Agent 页面批准连接。" : "连接后，可从当前页面保存字幕。";
  primary.textContent = paired ? "保存当前视频" : pairing ? "我已批准，完成连接" : "连接 Notebook Agent";
  primary.disabled = false;
  disconnect.hidden = !paired && !pairing;
}

primary.addEventListener("click", async () => {
  primary.disabled = true;
  statusNode.textContent = action === "capture" ? "正在读取并提交字幕…" : "正在连接…";
  const reply = await send(action === "capture" ? "CAPTURE" : action === "finish" ? "FINISH_PAIRING" : "START_PAIRING");
  if (!reply.ok) {
    statusNode.textContent = messages[reply.error ?? ""] ?? "操作没有完成，请稍后重试。";
    primary.disabled = false;
    return;
  }
  if (action === "capture") {
    const lifecycle = String(reply.result?.lifecycle ?? "queued");
    statusNode.textContent = lifecycle === "needs_asr" ? "视频没有可用字幕，已标记为需要语音转写。" : "已提交到资料库，Notebook Agent 正在整理字幕。";
    primary.textContent = "再次保存";
    primary.disabled = false;
  } else if (action === "pair") {
    action = "finish";
    statusNode.textContent = "请在新打开的网页中批准连接，然后回到这里完成。";
    primary.textContent = "我已批准，完成连接";
    primary.disabled = false;
    disconnect.hidden = false;
  } else {
    render({ ok: true, result: { paired: true } });
  }
});

disconnect.addEventListener("click", async () => { await send("DISCONNECT"); render({ ok: true, result: { paired: false } }); });
void send("STATUS").then(render);
