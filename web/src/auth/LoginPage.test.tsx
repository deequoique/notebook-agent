import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Capabilities } from "../api/contracts";
import { challengePollInterval, LoginPage } from "./LoginPage";

const capabilities: Capabilities = {
  supported_platforms: ["youtube"],
  browser_companion: true,
  web_login_channels: ["telegram", "wechat"],
  save_enabled: true,
  max_save_batch_size: 10,
  transcript_pagination: true,
  archive: true,
  summary_generation: false,
  chat: false,
};

describe("login page", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("shows only user-facing chat login options without developer placeholder copy", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage loadCapabilities={vi.fn().mockResolvedValue(capabilities)} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const wechat = await screen.findByRole("button", { name: "使用微信登录" });
    const telegram = screen.getByRole("button", { name: "使用 Telegram 登录" });
    expect(container.querySelector(".wordmark .brand-logo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "获取浏览器插件" })).toHaveAttribute(
      "href",
      "/assets/notebook-agent-browser-companion-production-0.1.3.zip",
    );
    expect(screen.getByRole("link", { name: "获取浏览器插件" })).toHaveAttribute("download");
    expect(within(wechat).getByTestId("wechat-brand-icon")).toBeInTheDocument();
    expect(within(telegram).getByTestId("telegram-brand-icon")).toBeInTheDocument();
    expect(screen.queryByText("主要方式")).not.toBeInTheDocument();
    expect(screen.queryByText("在已绑定的微信聊天中确认身份")).not.toBeInTheDocument();
    expect(screen.queryByText("通过已绑定的 Telegram 账号确认")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用账号密码登录" })).not.toBeInTheDocument();
    expect(screen.queryByText(/前端预留|查看预留|查看表单|当前版本|当前部署/)).not.toBeInTheDocument();
  });

  it("creates a channel challenge and keeps its browser secret in memory", async () => {
    const createChallenge = vi.fn().mockResolvedValue({
      public_id: "challenge-public",
      command: "/web-login ABCD-EFGH",
      browser_secret: "browser-only-secret",
      target_channel: "telegram",
      expires_at: "2026-08-07T12:10:00Z",
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
            createChallenge={createChallenge}
            getStatus={vi.fn().mockResolvedValue({ status: "pending", expires_at: "2026-08-07T12:10:00Z" })}
            exchangeSession={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "使用 Telegram 登录" }));

    expect(createChallenge).toHaveBeenCalledWith("telegram");
    expect(screen.getByRole("heading", { name: "登录你的视频资料库" })).toBeInTheDocument();
    expect(screen.getByText("请在 Telegram 中发送这条登录指令：")).toBeInTheDocument();
    expect(await screen.findByText("/web-login ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByText(/这条登录指令会在短时间后失效/)).toBeInTheDocument();
    expect(screen.queryByText(/10 分钟/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cookie|令牌/)).not.toBeInTheDocument();
    expect(screen.queryByText("browser-only-secret")).not.toBeInTheDocument();
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("exchanges an approved challenge and hands off without exposing session tokens", async () => {
    const onAuthenticated = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    const createChallenge = vi.fn().mockResolvedValue({
      public_id: "challenge-public",
      command: "/web-login ABCD-EFGH",
      browser_secret: "browser-only-secret",
      target_channel: "telegram",
      expires_at: "2026-08-07T12:10:00Z",
    });
    const exchangeSession = vi.fn().mockResolvedValue({
      authenticated: true,
      login_channel: "telegram",
      expires_at: "2026-09-07T12:00:00Z",
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
            createChallenge={createChallenge}
            getStatus={vi.fn().mockResolvedValue({ status: "approved", expires_at: "2026-08-07T12:10:00Z" })}
            exchangeSession={exchangeSession}
            onAuthenticated={onAuthenticated}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole("button", { name: "使用 Telegram 登录" }));

    expect(await screen.findByText("登录已确认，正在打开资料库…")).toBeInTheDocument();
    expect(exchangeSession).toHaveBeenCalledWith("challenge-public", "browser-only-secret");
    expect(onAuthenticated).toHaveBeenCalledOnce();
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("lets the user restart in place after challenge polling fails", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    const createChallenge = vi.fn().mockResolvedValue({
      public_id: "challenge-public",
      command: "/web-login ABCD-EFGH",
      browser_secret: "browser-only-secret",
      target_channel: "telegram",
      expires_at: "2026-08-07T12:10:00Z",
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
            createChallenge={createChallenge}
            getStatus={vi.fn().mockRejectedValue(new Error("network unavailable"))}
            exchangeSession={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "使用 Telegram 登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("登录没有完成，请重新获取登录指令");
    await user.click(screen.getByRole("button", { name: "重新获取" }));

    expect(screen.getByRole("button", { name: "使用 Telegram 登录" })).toBeEnabled();
    expect(screen.queryByText("/web-login ABCD-EFGH")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps both chat channels visible and disables channels not advertised by the server", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue({
              ...capabilities,
              web_login_channels: ["telegram"],
            })}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: "使用 Telegram 登录" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "使用微信登录" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "使用账号密码登录" })).not.toBeInTheDocument();
    expect(screen.getByText("暂不可用")).toBeInTheDocument();
  });

  it("keeps channel entries visible with a retryable status when capabilities fail", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const loadCapabilities = vi
      .fn()
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce(capabilities);
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage loadCapabilities={loadCapabilities} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("登录方式暂时无法加载");
    expect(screen.getByRole("button", { name: "使用 Telegram 登录" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "使用微信登录" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "使用账号密码登录" })).not.toBeInTheDocument();
    expect(screen.getAllByText("暂时无法连接")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "使用微信登录" })).toBeEnabled());
  });

  it("shows all login methods while capabilities are still loading", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage loadCapabilities={() => new Promise(() => undefined)} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("button", { name: "使用 Telegram 登录" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "使用微信登录" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "使用账号密码登录" })).not.toBeInTheDocument();
    expect(screen.getAllByText("正在检测")).toHaveLength(2);
    expect(screen.getByText("正在加载登录方式…")).toBeInTheDocument();
  });

  it("stops polling after an error even when the previous result was pending", () => {
    expect(challengePollInterval({
      state: {
        status: "error",
        data: { status: "pending", expires_at: "2026-08-07T12:10:00Z" },
      },
    })).toBe(false);
  });

  it("renders an enabled email flow when email is the only advertised capability", async () => {
    const emailCapabilities: Capabilities = {
      ...capabilities,
      web_login_channels: ["email"],
    };
    const requestEmailChallenge = vi.fn().mockResolvedValue({ status: "accepted" as const });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue(emailCapabilities)}
            requestEmailChallenge={requestEmailChallenge}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const email = await screen.findByRole("textbox", { name: "邮箱地址" });
    expect(screen.getByRole("button", { name: "获取验证码" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "使用微信登录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用 Telegram 登录" })).not.toBeInTheDocument();

    await user.type(email, "person@example.test");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));

    expect(requestEmailChallenge).toHaveBeenCalledWith("person@example.test");
    expect(await screen.findByRole("textbox", { name: "6 位验证码" })).toBeInTheDocument();
    expect(screen.getByText("如果该邮箱已绑定，验证码已发送，请查收邮件。"))
      .toBeInTheDocument();
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("validates six digits, keeps errors retryable, and completes verification", async () => {
    const onAuthenticated = vi.fn();
    const requestEmailChallenge = vi.fn().mockResolvedValue({ status: "accepted" as const });
    const verifyEmailChallenge = vi
      .fn()
      .mockRejectedValueOnce(new Error("safe server failure"))
      .mockResolvedValue({
        authenticated: true,
        login_channel: "email",
        expires_at: "2026-09-07T12:00:00Z",
      });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue({
              ...capabilities,
              web_login_channels: ["email"],
            })}
            requestEmailChallenge={requestEmailChallenge}
            verifyEmailChallenge={verifyEmailChallenge}
            onAuthenticated={onAuthenticated}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.type(await screen.findByRole("textbox", { name: "邮箱地址" }), "person@example.test");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    const code = await screen.findByRole("textbox", { name: "6 位验证码" });

    await user.type(code, "12");
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请输入 6 位数字验证码");
    expect(verifyEmailChallenge).not.toHaveBeenCalled();

    await user.clear(code);
    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    expect(verifyEmailChallenge).toHaveBeenCalledWith("person@example.test", "123456");
    expect(await screen.findByRole("alert")).toHaveTextContent("登录暂时没有完成");
    expect(onAuthenticated).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认登录" }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("prevents duplicate challenge submits and lets users change email", async () => {
    let resolveRequest: (value: { status: "accepted" }) => void = () => undefined;
    const requestEmailChallenge = vi.fn().mockImplementation(
      () => new Promise<{ status: "accepted" }>((resolve) => {
        resolveRequest = resolve;
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue({
              ...capabilities,
              web_login_channels: ["email"],
            })}
            requestEmailChallenge={requestEmailChallenge}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.type(await screen.findByRole("textbox", { name: "邮箱地址" }), "first@example.test");
    const send = screen.getByRole("button", { name: "获取验证码" });
    await user.click(send);
    await user.click(send);
    expect(requestEmailChallenge).toHaveBeenCalledTimes(1);
    expect(send).toBeDisabled();

    resolveRequest({ status: "accepted" });
    expect(await screen.findByRole("textbox", { name: "6 位验证码" })).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "6 位验证码" }), "123");
    await user.click(screen.getByRole("button", { name: /更换邮箱/ }));
    expect(screen.getByRole("textbox", { name: "邮箱地址" })).toHaveValue("first@example.test");
    expect(screen.queryByRole("textbox", { name: "6 位验证码" })).not.toBeInTheDocument();
  });

  it("announces a one-time account-link success notice without persisting route state", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[{
          pathname: "/login",
          state: { accountLinkSuccess: true },
        }]}
        >
          <LoginPage
            loadCapabilities={vi.fn().mockResolvedValue({
              ...capabilities,
              web_login_channels: ["email"],
            })}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("status")).toHaveTextContent("Telegram 已绑定");
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });
});
