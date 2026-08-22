import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useLocation } from "react-router";

import {
  createLoginChallenge,
  exchangeChallenge,
  getCapabilities,
  getChallengeStatus,
  requestEmailChallenge,
  verifyEmailChallenge,
} from "../api/client";
import type {
  Capabilities,
  ChallengeStatus,
  LegacyLoginChannel,
  LoginChallenge,
  LoginChannel,
  SessionInfo,
} from "../api/contracts";
import { BrandLogo } from "../app/BrandLogo";
import { BROWSER_COMPANION_DOWNLOAD_URL } from "../app/browserCompanion";
import { useRouteNavigate } from "../app/RouteTransition";

type ChannelAvailability = "checking" | "available" | "disabled" | "unavailable";
type EmailStep = "email" | "code";

function hasAccountLinkSuccess(state: unknown): boolean {
  return (
    typeof state === "object"
    && state !== null
    && "accountLinkSuccess" in state
    && (state as { accountLinkSuccess?: unknown }).accountLinkSuccess === true
  );
}

interface LoginMethodOptionProps {
  ariaLabel: string;
  icon: ReactNode;
  iconClassName?: string;
  title: string;
  status: string;
  statusTone?: "ready" | "muted" | "error";
  disabled?: boolean;
  onClick: () => void;
}

interface LoginPageProps {
  loadCapabilities?: () => Promise<Capabilities>;
  createChallenge?: (channel: LegacyLoginChannel) => Promise<LoginChallenge>;
  getStatus?: (publicId: string, browserSecret: string) => Promise<ChallengeStatus>;
  exchangeSession?: (publicId: string, browserSecret: string) => Promise<SessionInfo>;
  requestEmailChallenge?: (email: string) => Promise<{ status: "accepted" }>;
  verifyEmailChallenge?: (email: string, code: string) => Promise<SessionInfo>;
  onAuthenticated?: (session: SessionInfo) => void;
}

export function LoginPage({
  loadCapabilities = getCapabilities,
  createChallenge = createLoginChallenge,
  getStatus = getChallengeStatus,
  exchangeSession = exchangeChallenge,
  requestEmailChallenge: sendEmailChallenge = requestEmailChallenge,
  verifyEmailChallenge: confirmEmailChallenge = verifyEmailChallenge,
  onAuthenticated,
}: LoginPageProps) {
  const navigate = useRouteNavigate();
  const location = useLocation();
  const [accountLinkSuccess, setAccountLinkSuccess] = useState(false);
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [emailStep, setEmailStep] = useState<EmailStep>("email");
  const [emailError, setEmailError] = useState<string | null>(null);
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: loadCapabilities,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const mutation = useMutation({
    mutationFn: (channel: LegacyLoginChannel) => createChallenge(channel),
    onSuccess: setChallenge,
  });
  const status = useQuery({
    queryKey: ["login-challenge", challenge?.public_id],
    queryFn: () => getStatus(challenge!.public_id, challenge!.browser_secret),
    enabled: challenge !== null,
    retry: false,
    refetchInterval: challengePollInterval,
  });
  const exchange = useMutation({
    mutationFn: () => exchangeSession(challenge!.public_id, challenge!.browser_secret),
    onSuccess: activate,
  });
  const emailChallenge = useMutation({
    mutationFn: (value: string) => sendEmailChallenge(value),
    onSuccess: () => {
      setEmailStep("code");
      setCode("");
      setEmailError(null);
    },
  });
  const emailVerify = useMutation({
    mutationFn: ({ address, value }: { address: string; value: string }) =>
      confirmEmailChallenge(address, value),
    onSuccess: activate,
  });

  useEffect(() => {
    if (!hasAccountLinkSuccess(location.state)) return;
    setAccountLinkSuccess(true);
    // Consume the one-time notice from history immediately.  It carries no
    // token or account identifier and is never written to browser storage.
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  function activate(session: SessionInfo) {
    if (onAuthenticated) onAuthenticated(session);
    else navigate("/library", { replace: true });
  }

  useEffect(() => {
    if (status.data?.status === "approved" && exchange.isIdle) exchange.mutate();
  }, [status.data?.status, exchange]);

  const emailEnabled = Boolean(
    capabilities.data?.web_login_channels.includes("email"),
  );
  const loginFailed = mutation.isError || status.isError || exchange.isError;
  const telegramAvailability = channelAvailability(capabilities, "telegram");
  const wechatAvailability = channelAvailability(capabilities, "wechat");

  function startChannelLogin(channel: LegacyLoginChannel) {
    if (
      channelAvailability(capabilities, channel) !== "available" ||
      mutation.isPending
    ) return;
    mutation.mutate(channel);
  }

  function restartLogin() {
    setChallenge(null);
    mutation.reset();
    exchange.reset();
  }

  function resetEmailLogin() {
    setEmailStep("email");
    setCode("");
    setEmailError(null);
    emailChallenge.reset();
    emailVerify.reset();
  }

  function submitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = email.trim();
    if (value.length < 3 || !value.includes("@")) {
      setEmailError("请输入有效的邮箱地址。");
      return;
    }
    setEmailError(null);
    if (!emailChallenge.isPending) emailChallenge.mutate(value);
  }

  function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = code.trim();
    if (!/^\d{6}$/.test(value)) {
      setEmailError("请输入 6 位数字验证码。");
      return;
    }
    setEmailError(null);
    if (!emailVerify.isPending) {
      emailVerify.mutate({ address: email.trim(), value });
    }
  }

  const emailFailure = emailError ||
    (emailChallenge.isError || emailVerify.isError
      ? "登录暂时没有完成，请检查输入后重试。"
      : null);

  return (
    <main className="login-page">
      <div className="paper-glow" aria-hidden="true" />
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-card__topline">
          <a className="wordmark" href="/" aria-label="Notebook Agent 首页">
            <BrandLogo className="wordmark__sigil" />
            <span>Notebook Agent</span>
          </a>
          <a
            className="login-companion-link"
            download
            href={BROWSER_COMPANION_DOWNLOAD_URL}
          >
            获取浏览器插件
          </a>
        </div>
        <p className="eyebrow">你的私人视频资料库</p>
        <h1 id="login-title">登录你的视频资料库</h1>
        {accountLinkSuccess ? (
          <p className="login-success-note" role="status" aria-live="polite">
            Telegram 已绑定。请使用邮箱验证码重新登录，进入合并后的私人资料库。
          </p>
        ) : null}

        {emailEnabled ? (
          <EmailLoginFlow
            email={email}
            code={code}
            step={emailStep}
            pendingEmail={emailChallenge.isPending}
            pendingCode={emailVerify.isPending}
            error={emailFailure}
            onEmailChange={(value) => {
              setEmail(value);
              if (emailError) setEmailError(null);
            }}
            onCodeChange={(value) => {
              setCode(value.replace(/\D/g, "").slice(0, 6));
              if (emailError) setEmailError(null);
            }}
            onSubmitEmail={submitEmail}
            onSubmitCode={submitCode}
            onChangeEmail={resetEmailLogin}
          />
        ) : challenge ? (
          <div className="login-flow">
            <div className="challenge-card" aria-live="polite">
              <span className="step-number">01</span>
              <div>
                <p>
                  请在 {challenge.target_channel === "telegram" ? "Telegram" : "微信"} 中发送这条登录指令：
                </p>
                <code>{challenge.command}</code>
                <p className="muted">
                  {exchange.isSuccess
                    ? "登录已确认，正在打开资料库…"
                    : "发送后请留在本页；确认完成会自动进入资料库。这条登录指令会在短时间后失效。"}
                </p>
              </div>
            </div>
            <button className="login-back-button" type="button" onClick={restartLogin}>
              ← 更换登录方式
            </button>
          </div>
        ) : (
          <div className="login-method-panel">
            <div className="login-method-heading">
              <span>登录方式</span>
              <small>使用已绑定的聊天账号继续</small>
            </div>
            <div className="login-methods" aria-label="选择登录方式">
              <LoginMethodOption
                ariaLabel="使用微信登录"
                icon={<WechatBrandIcon />}
                iconClassName="login-method__icon--wechat"
                title="微信"
                status={channelStatusLabel(wechatAvailability, mutation.isPending && mutation.variables === "wechat")}
                statusTone={channelStatusTone(wechatAvailability)}
                disabled={wechatAvailability !== "available" || mutation.isPending}
                onClick={() => startChannelLogin("wechat")}
              />
              <LoginMethodOption
                ariaLabel="使用 Telegram 登录"
                icon={<TelegramBrandIcon />}
                iconClassName="login-method__icon--telegram"
                title="Telegram"
                status={channelStatusLabel(telegramAvailability, mutation.isPending && mutation.variables === "telegram")}
                statusTone={channelStatusTone(telegramAvailability)}
                disabled={telegramAvailability !== "available" || mutation.isPending}
                onClick={() => startChannelLogin("telegram")}
              />
            </div>
            {capabilities.isError ? (
              <div className="login-capability-error">
                <p className="inline-error" role="alert">登录方式暂时无法加载，请检查网络后重试。</p>
                <button
                  className="login-retry-button"
                  type="button"
                  aria-label="重试"
                  onClick={() => void capabilities.refetch()}
                >
                  重新加载登录方式
                </button>
              </div>
            ) : null}
            {capabilities.isPending ? (
              <p className="login-capability-note" aria-live="polite">正在加载登录方式…</p>
            ) : null}
          </div>
        )}
        {!emailEnabled && loginFailed ? (
          <div>
            <p className="inline-error" role="alert">
              {challenge ? "登录没有完成，请重新获取登录指令。" : "暂时无法开始登录，请重试。"}
            </p>
            {challenge ? (
              <button className="button button--quiet button--wide" type="button" onClick={restartLogin}>
                重新获取
              </button>
            ) : null}
          </div>
        ) : null}
        <p className="privacy-note">登录后只会显示你自己的资料库。</p>
      </section>
    </main>
  );
}

interface EmailLoginFlowProps {
  email: string;
  code: string;
  step: EmailStep;
  pendingEmail: boolean;
  pendingCode: boolean;
  error: string | null;
  onEmailChange: (value: string) => void;
  onCodeChange: (value: string) => void;
  onSubmitEmail: (event: FormEvent<HTMLFormElement>) => void;
  onSubmitCode: (event: FormEvent<HTMLFormElement>) => void;
  onChangeEmail: () => void;
}

function EmailLoginFlow({
  email,
  code,
  step,
  pendingEmail,
  pendingCode,
  error,
  onEmailChange,
  onCodeChange,
  onSubmitEmail,
  onSubmitCode,
  onChangeEmail,
}: EmailLoginFlowProps) {
  return (
    <div className="email-login-flow">
      {step === "email" ? (
        <form className="email-login-form" onSubmit={onSubmitEmail} noValidate>
          <div className="field">
            <label htmlFor="login-email">邮箱地址</label>
            <input
              id="login-email"
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              value={email}
              onChange={(event) => onEmailChange(event.target.value)}
              aria-describedby="login-email-help"
            />
            <p className="field-help" id="login-email-help">
              输入邮箱后，我们会发送一次性验证码。
            </p>
          </div>
          <button className="button button--primary button--wide" type="submit" disabled={pendingEmail}>
            {pendingEmail ? "正在发送…" : "获取验证码"}
          </button>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {pendingEmail ? <p className="login-capability-note" aria-live="polite">正在准备登录…</p> : null}
        </form>
      ) : (
        <form className="email-login-form" onSubmit={onSubmitCode} noValidate>
          <p className="email-login-confirmation" aria-live="polite">
            如果该邮箱已绑定，验证码已发送，请查收邮件。
          </p>
          <div className="field">
            <label htmlFor="login-code">6 位验证码</label>
            <input
              id="login-code"
              name="code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(event) => onCodeChange(event.target.value)}
              aria-describedby="login-code-help"
            />
            <p className="field-help" id="login-code-help">
              验证码为 6 位数字，仅在本页内使用。
            </p>
          </div>
          <button className="button button--primary button--wide" type="submit" disabled={pendingCode}>
            {pendingCode ? "正在验证…" : "确认登录"}
          </button>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          <button className="login-back-button" type="button" onClick={onChangeEmail}>
            ← 更换邮箱
          </button>
        </form>
      )}
    </div>
  );
}

function LoginMethodOption({
  ariaLabel,
  icon,
  iconClassName,
  title,
  status,
  statusTone = "muted",
  disabled = false,
  onClick,
}: LoginMethodOptionProps) {
  return (
    <button
      className="login-method"
      type="button"
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={onClick}
    >
      <span className={`login-method__icon${iconClassName ? ` ${iconClassName}` : ""}`} aria-hidden="true">
        {icon}
      </span>
      <span className="login-method__copy">
        <strong>{title}</strong>
      </span>
      <span className={`login-method__status login-method__status--${statusTone}`}>{status}</span>
    </button>
  );
}

function WechatBrandIcon() {
  return (
    <svg data-testid="wechat-brand-icon" viewBox="0 0 32 32" focusable="false">
      <path d="M13.5 5.4C7.3 5.4 2.3 9.6 2.3 14.8c0 2.9 1.6 5.5 4.1 7.2l-1 3.6 4-2c1.3.4 2.7.7 4.1.7h.7a8.8 8.8 0 0 1-.5-2.9c0-5.1 4.7-9.2 10.5-9.4-1.5-3.8-5.7-6.6-10.7-6.6Zm-3.8 7.1a1.5 1.5 1 1 1 0-3 1.5 1.5 0 0 1 0 3Zm7.5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z" />
      <path d="M29.7 21.4c0-4.2-4-7.6-8.8-7.6S12 17.2 12 21.4s4 7.6 8.9 7.6c1.2 0 2.3-.2 3.3-.6l3.2 1.7-.8-2.9a7.3 7.3 0 0 0 3.1-5.8Zm-11.8-1.2a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4Zm6 0a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4Z" />
    </svg>
  );
}

function TelegramBrandIcon() {
  return (
    <svg data-testid="telegram-brand-icon" viewBox="0 0 24 24" focusable="false">
      <path d="M21.7 3.4 18.5 20c-.2 1.2-.9 1.5-1.9.9l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.4-5 9.1-8.2c.4-.4-.1-.6-.6-.2L6 13.7l-4.8-1.5c-1.1-.3-1.1-1.1.2-1.6L20.2 3.3c.9-.3 1.7.2 1.5 1.1Z" />
    </svg>
  );
}

function channelAvailability(
  capabilities: { isPending: boolean; isError: boolean; data?: Capabilities },
  channel: LoginChannel,
): ChannelAvailability {
  if (capabilities.isPending) return "checking";
  if (capabilities.isError || !capabilities.data) return "unavailable";
  return capabilities.data.web_login_channels.includes(channel) ? "available" : "disabled";
}

function channelStatusLabel(availability: ChannelAvailability, isStarting: boolean): string {
  if (isStarting) return "正在创建…";
  switch (availability) {
    case "checking":
      return "正在检测";
    case "available":
      return "可以使用";
    case "disabled":
      return "暂不可用";
    case "unavailable":
      return "暂时无法连接";
  }
}

function channelStatusTone(availability: ChannelAvailability): "ready" | "muted" | "error" {
  switch (availability) {
    case "available":
      return "ready";
    case "unavailable":
      return "error";
    case "checking":
    case "disabled":
      return "muted";
  }
}

export function challengePollInterval(query: {
  state: { status: string; data?: ChallengeStatus };
}): number | false {
  if (query.state.status === "error") return false;
  return query.state.data?.status === "pending" ? 1_500 : false;
}
