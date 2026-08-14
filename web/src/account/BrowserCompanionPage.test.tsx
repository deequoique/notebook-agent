import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  approve: vi.fn(),
  list: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock("../api/client", () => {
  class MockApiError extends Error {
    status = 500;
    code = "request_failed";
  }
  return {
    ApiError: MockApiError,
    approveBrowserCompanionPairing: api.approve,
    listBrowserCompanionDevices: api.list,
    revokeBrowserCompanionDevice: api.revoke,
  };
});

import { BrowserCompanionPage } from "./BrowserCompanionPage";

function renderPage(path = "/account/browser-companion") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><BrowserCompanionPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("browser companion account page", () => {
  beforeEach(() => {
    api.approve.mockReset().mockResolvedValue({ pairing_id: "a".repeat(32), status: "approved", expires_at: new Date().toISOString() });
    api.list.mockReset().mockResolvedValue({ devices: [] });
    api.revoke.mockReset().mockResolvedValue(undefined);
  });

  it("explains the optional coexistence model without a global switch", async () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "浏览器伴侣" })).toBeInTheDocument();
    expect(screen.getByText(/原有的 YouTube 保存方式不会被替换/)).toBeInTheDocument();
    expect(await screen.findByText("目前没有已连接的插件。")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("approves the public pairing id and can revoke a listed device", async () => {
    const user = userEvent.setup();
    api.list.mockResolvedValue({
      devices: [{
        device_id: "device-public",
        client_label: "Chrome / Chromium",
        client_version: "0.1.0",
        expires_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        last_used_at: null,
        revoked_at: null,
      }],
    });
    renderPage(`/account/browser-companion?pairing=${"a".repeat(32)}`);

    await user.click(screen.getByRole("button", { name: "允许连接" }));
    expect(api.approve).toHaveBeenCalledWith("a".repeat(32));
    expect(await screen.findByRole("status")).toHaveTextContent("已批准");

    await user.click(await screen.findByRole("button", { name: "断开连接" }));
    expect(api.revoke.mock.calls[0]?.[0]).toBe("device-public");
  });
});
