import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

describe("application shell", () => {
  it("keeps one quiet top bar and exposes an explicit logout", async () => {
    const onLogout = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter>
        <AppShell loginChannel="telegram" onLogout={onLogout}>
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );

    expect(screen.getAllByText("Notebook Agent")).toHaveLength(1);
    expect(container.querySelector(".wordmark .brand-logo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("heading", { name: "我的资料库" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("打开账户菜单，当前登录方式：Telegram"));
    expect(screen.queryByText("TG")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "绑定 Telegram" })).toHaveAttribute(
      "href",
      "/account/link",
    );
    expect(screen.getByRole("link", { name: "浏览器伴侣" })).toHaveAttribute(
      "href",
      "/account/browser-companion",
    );
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(onLogout).toHaveBeenCalledOnce();
  });

  it("closes the account menu when the user clicks outside it", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AppShell loginChannel="telegram" onLogout={() => undefined}>
          <button type="button">资料库操作</button>
        </AppShell>
      </MemoryRouter>,
    );

    const trigger = screen.getByLabelText("打开账户菜单，当前登录方式：Telegram");
    const menu = trigger.closest("details");
    await user.click(trigger);
    expect(menu).toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "资料库操作" }));
    expect(menu).not.toHaveAttribute("open");
  });

  it("keeps a failed logout visible without pretending the session ended", async () => {
    render(
      <MemoryRouter>
        <AppShell
          loginChannel="wechat"
          logoutError="退出失败，请检查网络后重试。"
          onLogout={() => undefined}
        >
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByLabelText("打开账户菜单，当前登录方式：微信"));
    expect(screen.getByRole("alert")).toHaveTextContent("退出失败，请检查网络后重试。");
  });
});
