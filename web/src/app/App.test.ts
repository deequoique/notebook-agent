import { QueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import {
  App,
  browserCompanionReturnTo,
  createPrivateQueryClient,
  createSessionQueryClient,
  endPrivateSession,
  loginReturnTo,
  logoutAndClear,
} from "./App";

describe("private cache boundary", () => {
  it("clears all cached tenant data after a successful logout", async () => {
    const client = new QueryClient();
    client.setQueryData(["library"], { private: "previous-user" });
    const navigate = vi.fn();

    await logoutAndClear(client, vi.fn().mockResolvedValue(undefined), navigate);

    expect(client.getQueryData(["library"])).toBeUndefined();
    expect(navigate).toHaveBeenCalledWith("/login", { replace: true });
  });

  it("keeps the private session visible when the server did not confirm logout", async () => {
    const client = new QueryClient();
    client.setQueryData(["library-item", "x"], { private: "previous-user" });
    const navigate = vi.fn();

    await expect(
      logoutAndClear(client, vi.fn().mockRejectedValue(new Error("session expired")), navigate),
    ).rejects.toThrow("session expired");

    expect(client.getQueryData(["library-item", "x"])).toEqual({ private: "previous-user" });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("rotates the cache so a late old-session mutation cannot rehydrate the active tenant", async () => {
    const oldClient = createPrivateQueryClient();
    const replacementClient = createPrivateQueryClient();
    let activeClient = oldClient;
    let resolveMutation: (value: { private: string }) => void = () => undefined;
    const lateResult = new Promise<{ private: string }>((resolve) => {
      resolveMutation = resolve;
    });
    const mutation = oldClient.getMutationCache().build(oldClient, {
      mutationFn: () => lateResult,
      onSuccess: (value) => oldClient.setQueryData(["library-item", "old"], value),
    });
    const pendingMutation = mutation.execute(undefined);

    await logoutAndClear(
      oldClient,
      vi.fn().mockResolvedValue(undefined),
      vi.fn(),
      () => { activeClient = replacementClient; },
    );
    resolveMutation({ private: "previous-user" });
    await pendingMutation;

    expect(oldClient.getQueryData(["library-item", "old"])).toEqual({ private: "previous-user" });
    expect(activeClient).toBe(replacementClient);
    expect(activeClient.getQueryData(["library-item", "old"])).toBeUndefined();
  });

  it("activates a new login in a fresh cache without copying the previous tenant", () => {
    const oldClient = createPrivateQueryClient();
    oldClient.setQueryData(["library"], { private: "previous-user" });
    const session = {
      authenticated: true as const,
      login_channel: "email" as const,
      expires_at: "2026-09-06T10:00:00Z",
    };

    const nextClient = createSessionQueryClient(session);

    expect(nextClient).not.toBe(oldClient);
    expect(nextClient.getQueryData(["library"])).toBeUndefined();
    expect(nextClient.getQueryData(["session"])).toEqual(session);
  });

  it("returns from a successful account link with an ephemeral notice after rotating cache", () => {
    const client = createPrivateQueryClient();
    client.setQueryData(["library"], { private: "previous-user" });
    const navigate = vi.fn();
    const rotateClient = vi.fn();

    endPrivateSession(client, navigate, rotateClient, { accountLinkSuccess: true });

    expect(client.getQueryData(["library"])).toBeUndefined();
    expect(rotateClient).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith("/login", {
      replace: true,
      state: { accountLinkSuccess: true },
    });
  });

  it("keeps a late old-session mutation isolated after a successful account link", async () => {
    const oldClient = createPrivateQueryClient();
    const replacementClient = createPrivateQueryClient();
    let activeClient = oldClient;
    let resolveMutation: (value: { private: string }) => void = () => undefined;
    const lateResult = new Promise<{ private: string }>((resolve) => {
      resolveMutation = resolve;
    });
    const mutation = oldClient.getMutationCache().build(oldClient, {
      mutationFn: () => lateResult,
      onSuccess: (value) => oldClient.setQueryData(["library-item", "absorbed"], value),
    });
    const pendingMutation = mutation.execute(undefined);

    endPrivateSession(
      oldClient,
      vi.fn(),
      () => { activeClient = replacementClient; },
      { accountLinkSuccess: true },
    );
    resolveMutation({ private: "absorbed-tenant" });
    await pendingMutation;

    expect(oldClient.getQueryData(["library-item", "absorbed"])).toEqual({
      private: "absorbed-tenant",
    });
    expect(activeClient).toBe(replacementClient);
    expect(activeClient.getQueryData(["library-item", "absorbed"])).toBeUndefined();
  });

  it("rejects direct unauthenticated access to the account-link route", async () => {
    window.history.replaceState({}, "", "/account/link");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") {
        return new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/capabilities") {
        return new Response(JSON.stringify({ web_login_channels: ["email"] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(createElement(App));

    expect(await screen.findByRole("heading", { name: "登录你的视频资料库" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "绑定 Telegram" })).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("allows an authenticated user to open the account-link route", async () => {
    window.history.replaceState({}, "", "/account/link");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        authenticated: true,
        login_channel: "email",
        expires_at: "2026-08-10T12:00:00Z",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(createElement(App));

    expect(await screen.findByRole("heading", { name: "绑定 Telegram" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/account/link");
  });

  it("keeps only a valid browser-companion destination through login", () => {
    const pairing = "a".repeat(32);
    expect(browserCompanionReturnTo(
      "/account/browser-companion",
      `?pairing=${pairing}`,
    )).toBe(`/account/browser-companion?pairing=${pairing}`);
    expect(browserCompanionReturnTo(
      "/account/browser-companion",
      "?pairing=invalid&next=https://example.com",
    )).toBe("/account/browser-companion");
    expect(loginReturnTo({ returnTo: "https://example.com" })).toBe("/library");
    expect(loginReturnTo({ returnTo: `/account/browser-companion?pairing=${pairing}` }))
      .toBe(`/account/browser-companion?pairing=${pairing}`);
  });

  it("returns an email login to the pending browser-companion approval", async () => {
    const pairing = "b".repeat(32);
    window.history.replaceState({}, "", `/account/browser-companion?pairing=${pairing}`);
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") {
        return new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/capabilities") {
        return new Response(JSON.stringify({
          supported_platforms: ["youtube", "bilibili", "ntu_kaltura"],
          browser_companion: true,
          web_login_channels: ["email"],
          save_enabled: true,
          max_save_batch_size: 10,
          transcript_pagination: true,
          archive: true,
          summary_generation: false,
          chat: false,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (path === "/api/v1/auth/challenges" && init?.method === "POST") {
        return new Response(JSON.stringify({ status: "accepted" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/auth/verify" && init?.method === "POST") {
        return new Response(JSON.stringify({
          authenticated: true,
          login_channel: "email",
          expires_at: "2026-09-14T12:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (path === "/api/v1/browser-companion/devices") {
        return new Response(JSON.stringify({ devices: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(createElement(App));
    const user = userEvent.setup();
    await user.type(await screen.findByRole("textbox", { name: "邮箱地址" }), "local-user@example.test");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByRole("textbox", { name: "6 位验证码" }), "123456");
    await user.click(screen.getByRole("button", { name: "确认登录" }));

    expect(await screen.findByRole("heading", { name: "连接这个插件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "允许连接" })).toBeEnabled();
    expect(window.location.pathname).toBe("/account/browser-companion");
    expect(window.location.search).toBe(`?pairing=${pairing}`);
  });
});
