import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { Capabilities, ConversationHistoryPage, ConversationResponse, ConversationTurns } from "../api/contracts";
import { ApiError } from "../api/client";
import { ChatPage } from "./ChatPage";

const history: ConversationHistoryPage = {
  items: [{
    thread_id: "thread-1",
    conversation_id: "conversation-1",
    title: "用户访谈应该怎样做？",
    preview: "先明确你要验证的假设。",
    updated_at: "2026-08-10T10:00:00Z",
  }],
  next_cursor: null,
};

const turns: ConversationTurns = {
  thread_id: "thread-1",
  conversation_id: "conversation-1",
  turns: [{
    user_text: "用户访谈应该怎样做？",
    assistant_text: "先明确你要验证的假设。\n\n来源：\n- 访谈方法\n  - [S42] https://example.test/video — 从开放问题开始。",
    status: "ok",
    error_code: null,
    citations: [{
      title: "访谈方法",
      excerpt: "从开放问题开始。",
      url: "https://example.test/video",
      start_sec: 65,
    }],
    action_results: [],
    created_at: "2026-08-10T10:00:00Z",
  }],
};

const capabilities: Capabilities = {
  supported_platforms: ["youtube"],
  browser_companion: true,
  web_login_channels: ["email"],
  save_enabled: false,
  max_save_batch_size: 10,
  transcript_pagination: true,
  archive: false,
  summary_generation: false,
  chat: true,
};

function renderPage(props: Partial<Parameters<typeof ChatPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ChatPage
          loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
          fetchHistory={vi.fn().mockResolvedValue(history)}
          fetchTurns={vi.fn().mockResolvedValue(turns)}
          reset={vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null })}
          send={vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null })}
          createId={() => "new-id"}
          {...props}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AI search chat page", () => {
  it("bootstraps an empty history into an enabled composer without showing a history error", async () => {
    const user = userEvent.setup();
    const emptyHistory: ConversationHistoryPage = { items: [], next_cursor: null };
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    const send = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue(emptyHistory),
      fetchTurns: vi.fn().mockResolvedValue({ thread_id: "thread-new", conversation_id: "new-id", turns: [] }),
      reset,
      send,
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(reset).toHaveBeenCalledWith("new-id"));
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.queryByText("历史对话加载失败。")).not.toBeInTheDocument();

    await user.click(input);
    await user.type(input, "空历史时也能提问吗？");
    expect(input).toHaveValue("空历史时也能提问吗？");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));
    expect(send).toHaveBeenCalledWith("new-id", {
      message_id: "new-id",
      text: "空历史时也能提问吗？",
    });
  });

  it("shows the history error only when the history request actually fails", async () => {
    const view = renderPage({ fetchHistory: vi.fn().mockRejectedValue(new Error("network unavailable")) });

    expect(await screen.findByText("历史对话加载失败。", {}, { timeout: 2_500 })).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    await waitFor(() => expect(view.container.querySelector(".chat-conversation")).toHaveAttribute("aria-busy", "false"));
  });

  it("selects the latest persisted conversation and renders its safe citations", async () => {
    const fetchTurns = vi.fn().mockResolvedValue(turns);
    renderPage({ fetchTurns });

    await waitFor(() => expect(fetchTurns).toHaveBeenCalledWith("thread-1"));
    await waitFor(() => expect(screen.getAllByText("先明确你要验证的假设。")).toHaveLength(2));
    expect(screen.queryByText(/来源：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\[S42\]/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /访谈方法/ })).toHaveAttribute("href", "https://example.test/video");
    expect(screen.getByText(/1:05/)).toBeInTheDocument();
  });

  it("renders a compact plain-text sidebar preview without Markdown markers", async () => {
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue({
        items: [{
          ...history.items![0],
          preview: "## 演讲大纲\n\n- **开场**：介绍 `樟宜机场`。 [S42]",
        }],
        next_cursor: null,
      }),
    });

    expect(await screen.findByText("演讲大纲 开场：介绍 樟宜机场。")).toBeInTheDocument();
    expect(screen.queryByText(/##|\*\*|`|\[S42\]/)).not.toBeInTheDocument();
  });

  it("sends a question through the selected durable conversation and prevents duplicate submit", async () => {
    const user = userEvent.setup();
    let resolveSend: (value: ConversationResponse) => void;
    const send = vi.fn<
      (conversationId: string, input: { message_id: string; text: string }) => Promise<ConversationResponse>
    >().mockImplementation(() => new Promise((resolve) => { resolveSend = resolve; }));
    renderPage({ send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "怎样做有效访谈？");
    const submit = screen.getByRole("button", { name: /发送问题/ });
    await user.click(submit);

    expect(send).toHaveBeenCalledWith("conversation-1", {
      message_id: "new-id",
      text: "怎样做有效访谈？",
    });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(send).toHaveBeenCalledOnce();
    resolveSend!({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("describes a server timeout without claiming the network failed", async () => {
    const user = userEvent.setup();
    renderPage({
      send: vi.fn().mockRejectedValue(new ApiError(504, "request_failed", "请求无法完成")),
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "拟定四分钟演讲大纲");
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("回答生成超时，请重新发送。");
    expect(screen.queryByText(/检查网络/)).not.toBeInTheDocument();
  });

  it("creates a fresh server conversation from the sidebar control", async () => {
    const user = userEvent.setup();
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({ reset });

    await user.click(await screen.findByRole("button", { name: "新建检索" }));
    expect(reset).toHaveBeenCalledWith("new-id");
  });

  it("fills the composer from a concrete example question", async () => {
    const user = userEvent.setup();
    renderPage({
      fetchTurns: vi.fn().mockResolvedValue({
        thread_id: "thread-1",
        conversation_id: "conversation-1",
        turns: [],
      }),
    });

    const suggestion = await screen.findByRole("button", {
      name: /这个观点在哪个视频的什么位置/,
    });
    await user.click(suggestion);

    expect(screen.getByRole("textbox", { name: "向资料库提问" })).toHaveValue(
      "这个观点在哪个视频的什么位置？",
    );
  });

  it("does not load conversations when this deployment has chat disabled", async () => {
    const fetchHistory = vi.fn().mockResolvedValue(history);
    renderPage({
      loadCapabilities: vi.fn().mockResolvedValue({ ...capabilities, chat: false }),
      fetchHistory,
    });

    expect(await screen.findByRole("heading", { name: "尚未开放 AI 智能检索" })).toBeInTheDocument();
    expect(fetchHistory).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "新建检索" })).toBeDisabled();
  });
});
