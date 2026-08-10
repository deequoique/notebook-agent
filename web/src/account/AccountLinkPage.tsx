import { useMutation } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";

import { ApiError, consumeLinkToken, createTelegramLinkToken } from "../api/client";
import type { LinkTokenResponse, LinkedResponse } from "../api/contracts";

const LINK_TOKEN_MAX_LENGTH = 128;
const TELEGRAM_BOT_HANDLE = "@notebook_agent_bot";
const TELEGRAM_BOT_URL = "https://t.me/notebook_agent_bot";

interface AccountLinkPageProps {
  createToken?: () => Promise<LinkTokenResponse>;
  consumeToken?: (token: string) => Promise<LinkedResponse>;
  onLinked: () => void;
}

type CopyStatus = "copied" | "fallback" | null;

const LINK_ERROR_MESSAGES: Record<string, string> = {
  link_token_used: "该绑定码已使用，请在 Telegram Bot 中重新发送 /link web 获取新的绑定码。",
  link_token_expired: "该绑定码已过期，请在 Telegram Bot 中重新发送 /link web 获取新的绑定码。",
  link_channel_mismatch: "该绑定码的目标渠道不匹配，请回到 Telegram Bot 重新获取。",
  link_merge_busy: "目标账户仍有内容正在处理，请稍后使用同一个绑定码重试。",
  link_account_disabled: "账户当前不可用，暂时无法完成绑定。",
  link_source_unbound: "Telegram 账户尚未注册，请先在 Bot 中发送 /start 后再试。",
  link_merge_conflict: "账户状态发生变化，请在 Telegram Bot 中重新获取绑定码后再试。",
  link_token_invalid: "绑定码无效，请检查后重试。",
  session_invalid: "登录已失效，请重新登录。",
};

export function linkErrorMessage(error: unknown, fallback = "绑定暂时没有完成，请稍后重试。"): string {
  if (error instanceof ApiError) return LINK_ERROR_MESSAGES[error.code] ?? fallback;
  return fallback;
}

export function AccountLinkPage({
  createToken = createTelegramLinkToken,
  consumeToken = consumeLinkToken,
  onLinked,
}: AccountLinkPageProps) {
  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenInputError, setTokenInputError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<CopyStatus>(null);
  const commandRef = useRef<HTMLElement>(null);

  const createMutation = useMutation({
    mutationFn: createToken,
    // Link tokens are credentials. Remove the mutation response as soon as
    // this observer is replaced or unmounted instead of retaining it for the
    // default MutationCache garbage-collection window.
    gcTime: 0,
    onSuccess: ({ token }) => {
      setGeneratedToken(token);
      setCopyStatus(null);
    },
  });
  const consumeMutation = useMutation({
    mutationFn: (token: string) => consumeToken(token),
    // The pasted token is also stored as mutation variables, so it needs the
    // same immediate in-memory teardown when the user leaves this feature.
    gcTime: 0,
    onSuccess: (result) => {
      if (result.linked) onLinked();
      else setTokenInputError("绑定暂时没有完成，请稍后重试。");
    },
  });

  const command = generatedToken ? `/link ${generatedToken}` : null;
  const createError = createMutation.isError
    ? linkErrorMessage(createMutation.error, "暂时无法生成绑定码，请稍后重试。")
    : null;
  const consumeError = consumeMutation.isError
    ? linkErrorMessage(consumeMutation.error)
    : null;

  function generateToken() {
    if (!createMutation.isPending) createMutation.mutate();
  }

  async function copyCommand() {
    if (!command) return;
    try {
      const writeText = navigator.clipboard?.writeText;
      if (!writeText) throw new Error("clipboard_unavailable");
      await writeText.call(navigator.clipboard, command);
      setCopyStatus("copied");
    } catch {
      // Keep the command visible for keyboard selection when clipboard access
      // is unavailable or denied by the browser.
      commandRef.current?.focus();
      setCopyStatus("fallback");
    }
  }

  function submitToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (consumeMutation.isPending) return;
    const value = tokenDraft.trim();
    if (!value) {
      setTokenInputError("请输入 Telegram Bot 返回的绑定码。");
      consumeMutation.reset();
      return;
    }
    if (value.length > LINK_TOKEN_MAX_LENGTH) {
      setTokenInputError("绑定码过长，请检查后重试。");
      consumeMutation.reset();
      return;
    }
    setTokenInputError(null);
    consumeMutation.mutate(value);
  }

  return (
    <section className="account-link-page" aria-labelledby="account-link-title">
      <header className="account-link-page__heading">
        <p className="eyebrow">账户设置</p>
        <h1 id="account-link-title">绑定 Telegram</h1>
        <p>
          绑定后，Web 邮箱账户和 Telegram Bot 会共用同一份私人资料库；两个渠道的聊天历史仍然独立保存。
        </p>
      </header>

      <div className="account-link-grid">
        <section className="account-link-card" aria-labelledby="account-link-web-title">
          <p className="step-number" aria-hidden="true">01</p>
          <h2 id="account-link-web-title">从 Web 发起绑定</h2>
          <p>生成一次性绑定码，然后在配置好的 Telegram Bot 私聊中发送完整指令。</p>
          <p className="account-link-bot">
            Telegram Bot：
            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${TELEGRAM_BOT_HANDLE}（在新标签页打开）`}
            >
              {TELEGRAM_BOT_HANDLE}
            </a>
          </p>
          <button
            className="button button--primary button--wide"
            type="button"
            onClick={generateToken}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending
              ? "正在生成…"
              : generatedToken
                ? "重新生成绑定码"
                : "生成 Telegram 绑定码"}
          </button>
          {command ? (
            <div className="account-link-command" aria-live="polite">
              <p>在 Telegram Bot 私聊中发送：</p>
              <code ref={commandRef} tabIndex={0}>{command}</code>
              <button className="button button--quiet button--wide" type="button" onClick={() => void copyCommand()}>
                复制指令
              </button>
              {copyStatus === "copied" ? (
                <p className="account-link-status" role="status">指令已复制，可以粘贴到 Telegram Bot。</p>
              ) : null}
              {copyStatus === "fallback" ? (
                <p className="account-link-status" role="status">浏览器未允许复制，请选中上方指令后手动复制。</p>
              ) : null}
              <p className="field-help">
                绑定码短期有效，只能使用一次，请勿转发给他人。重新生成不会在页面上声明旧码已撤销，最终状态以后端为准。
              </p>
            </div>
          ) : (
            <p className="field-help">绑定码短期有效，只能使用一次，请勿转发给他人。</p>
          )}
          {createError ? <p className="inline-error" role="alert">{createError}</p> : null}
        </section>

        <section className="account-link-card" aria-labelledby="account-link-telegram-title">
          <p className="step-number" aria-hidden="true">02</p>
          <h2 id="account-link-telegram-title">从 Telegram 发起绑定</h2>
          <p>
            先在 Telegram Bot 私聊中发送 <code>/link web</code>，再把 Bot 返回的绑定码粘贴到这里。
          </p>
          <p className="account-link-bot">
            打开 Telegram Bot：
            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${TELEGRAM_BOT_HANDLE}（在新标签页打开）`}
            >
              {TELEGRAM_BOT_HANDLE}
            </a>
          </p>
          <form className="account-link-form" onSubmit={submitToken} noValidate>
            <div className="field">
              <label htmlFor="telegram-link-token">Telegram 绑定码</label>
              <input
                id="telegram-link-token"
                name="telegram-link-token"
                type="text"
                autoComplete="off"
                spellCheck={false}
                maxLength={LINK_TOKEN_MAX_LENGTH}
                value={tokenDraft}
                onChange={(event) => {
                  setTokenDraft(event.target.value);
                  if (tokenInputError) setTokenInputError(null);
                  if (consumeMutation.isError) consumeMutation.reset();
                }}
                aria-describedby="telegram-link-token-help"
              />
              <p className="field-help" id="telegram-link-token-help">
                只在本页临时使用；格式、有效期和账户归并状态由服务器校验。
              </p>
            </div>
            <button className="button button--primary button--wide" type="submit" disabled={consumeMutation.isPending}>
              {consumeMutation.isPending ? "正在绑定…" : "确认绑定"}
            </button>
            {tokenInputError ? <p className="inline-error" role="alert">{tokenInputError}</p> : null}
            {consumeError ? <p className="inline-error" role="alert">{consumeError}</p> : null}
            {consumeMutation.isPending ? (
              <p className="account-link-status" aria-live="polite">正在确认账户归并，请不要重复提交。</p>
            ) : null}
          </form>
        </section>
      </div>

      <p className="account-link-warning" role="note">
        绑定码和 Bot 凭据不会保存到浏览器、网址或聊天记录之外的 Notebook Agent 日志中。
      </p>
    </section>
  );
}
