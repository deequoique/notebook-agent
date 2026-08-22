import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { AccountLinkPage, linkErrorMessage } from "./AccountLinkPage";

function renderPage(
  props: Partial<React.ComponentProps<typeof AccountLinkPage>> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AccountLinkPage onLinked={vi.fn()} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

describe("Telegram account linking page", () => {
  it("generates and displays a Telegram command without offering WeChat", async () => {
    const user = userEvent.setup();
    const createToken = vi.fn().mockResolvedValue({ token: "temporary-token" });

    renderPage({ createToken });

    expect(screen.getByRole("heading", { name: "绑定 Telegram" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从 Web 发起绑定" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从 Telegram 发起绑定" })).toBeInTheDocument();
    expect(screen.queryByText(/微信/)).not.toBeInTheDocument();
    expect(screen.getByText(/共用同一份私人资料库/)).toBeInTheDocument();

    const botLinks = screen.getAllByRole("link", {
      name: "@notebook_agent_bot（在新标签页打开）",
    });
    expect(botLinks).toHaveLength(2);
    for (const link of botLinks) {
      expect(link).toHaveAttribute("href", "https://t.me/notebook_agent_bot");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(link).toHaveTextContent("@notebook_agent_bot");
    }

    await user.click(screen.getByRole("button", { name: "生成 Telegram 绑定码" }));

    expect(createToken).toHaveBeenCalledOnce();
    expect(await screen.findByText("/link temporary-token")).toBeInTheDocument();
    for (const link of screen.getAllByRole("link", {
      name: "@notebook_agent_bot（在新标签页打开）",
    })) {
      expect(link).toHaveAttribute("href", "https://t.me/notebook_agent_bot");
      expect(link.getAttribute("href")).not.toContain("temporary-token");
    }
    expect(screen.getByText(/短期有效，只能使用一次/)).toBeInTheDocument();
    expect(screen.getByLabelText("Telegram 绑定码")).toBeInTheDocument();
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
  });

  it("copies the visible command and leaves a manual-selection fallback", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderPage({ createToken: vi.fn().mockResolvedValue({ token: "copy-token" }) });

    await user.click(screen.getByRole("button", { name: "生成 Telegram 绑定码" }));
    await user.click(await screen.findByRole("button", { name: "复制指令" }));

    expect(writeText).toHaveBeenCalledWith("/link copy-token");
    expect(screen.getByRole("status")).toHaveTextContent("指令已复制");

    const fallbackWrite = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: fallbackWrite },
    });
    await user.click(screen.getByRole("button", { name: "复制指令" }));
    expect(screen.getByRole("status")).toHaveTextContent("手动复制");
    expect(screen.getByText("/link copy-token")).toHaveFocus();
  });

  it("prevents duplicate generation and duplicate consume submits while pending", async () => {
    const user = userEvent.setup();
    let resolveCreate: (value: { token: string }) => void = () => undefined;
    const createToken = vi.fn().mockImplementation(
      () => new Promise<{ token: string }>((resolve) => { resolveCreate = resolve; }),
    );
    let resolveConsume: (value: { linked: true }) => void = () => undefined;
    const consumeToken = vi.fn().mockImplementation(
      () => new Promise<{ linked: true }>((resolve) => { resolveConsume = resolve; }),
    );
    renderPage({ createToken, consumeToken });

    const generate = screen.getByRole("button", { name: "生成 Telegram 绑定码" });
    await user.click(generate);
    await user.click(generate);
    expect(createToken).toHaveBeenCalledOnce();
    expect(generate).toBeDisabled();
    resolveCreate({ token: "generated-token" });
    await screen.findByText("/link generated-token");

    const input = screen.getByRole("textbox", { name: "Telegram 绑定码" });
    await user.type(input, "  incoming-token  ");
    const submit = screen.getByRole("button", { name: "确认绑定" });
    await user.click(submit);
    await user.click(submit);
    expect(consumeToken).toHaveBeenCalledTimes(1);
    expect(consumeToken).toHaveBeenCalledWith("incoming-token");
    expect(submit).toBeDisabled();
    resolveConsume({ linked: true });
  });

  it("keeps a merge-busy token retryable and maps safe server errors", async () => {
    const user = userEvent.setup();
    const consumeToken = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, "link_merge_busy", "目标账户仍有内容正在处理"))
      .mockResolvedValue({ linked: true });
    const onLinked = vi.fn();
    renderPage({ consumeToken, onLinked });

    const input = screen.getByRole("textbox", { name: "Telegram 绑定码" });
    await user.type(input, "retryable-token");
    await user.click(screen.getByRole("button", { name: "确认绑定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请稍后使用同一个绑定码重试");
    expect(input).toHaveValue("retryable-token");

    await user.click(screen.getByRole("button", { name: "确认绑定" }));
    await waitFor(() => expect(onLinked).toHaveBeenCalledOnce());
    expect(consumeToken).toHaveBeenNthCalledWith(2, "retryable-token");
  });

  it("validates an empty token before making a request", async () => {
    const user = userEvent.setup();
    const consumeToken = vi.fn();
    renderPage({ consumeToken });

    await user.click(screen.getByRole("button", { name: "确认绑定" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入 Telegram Bot 返回的绑定码");
    expect(consumeToken).not.toHaveBeenCalled();
  });

  it.each([
    ["link_token_used", "已使用"],
    ["link_token_expired", "已过期"],
    ["link_channel_mismatch", "目标渠道不匹配"],
    ["link_merge_busy", "同一个绑定码重试"],
    ["link_account_disabled", "账户当前不可用"],
    ["link_source_unbound", "发送 /start"],
    ["link_merge_conflict", "账户状态发生变化"],
    ["link_token_invalid", "绑定码无效"],
    ["session_invalid", "登录已失效"],
  ])("maps %s to safe actionable copy", (code, expected) => {
    expect(linkErrorMessage(new ApiError(409, code, "sensitive server detail"))).toContain(expected);
  });

  it("does not expose unknown server error details", () => {
    expect(
      linkErrorMessage(new ApiError(500, "unexpected_failure", "sensitive server detail")),
    ).toBe("绑定暂时没有完成，请稍后重试。");
  });

  it("removes generated and pasted tokens from mutation memory on unmount", async () => {
    const user = userEvent.setup();
    const consumeToken = vi.fn().mockRejectedValue(
      new ApiError(409, "link_merge_busy", "目标账户仍有内容正在处理"),
    );
    const { client, unmount } = renderPage({
      createToken: vi.fn().mockResolvedValue({ token: "generated-secret" }),
      consumeToken,
    });

    await user.click(screen.getByRole("button", { name: "生成 Telegram 绑定码" }));
    await screen.findByText("/link generated-secret");
    await user.type(screen.getByRole("textbox", { name: "Telegram 绑定码" }), "pasted-secret");
    await user.click(screen.getByRole("button", { name: "确认绑定" }));
    await screen.findByRole("alert");

    expect(client.getMutationCache().getAll()).toHaveLength(2);
    unmount();
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });
});
