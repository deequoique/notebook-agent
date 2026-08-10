import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ConversationHistoryPage, ConversationResponse, ConversationTurns } from "../api/contracts";
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
    assistant_text: "先明确你要验证的假设。",
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

function renderPage(props: Partial<Parameters<typeof ChatPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ChatPage
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
    await user.click(screen.getByRole("button", { name: "发送问题" }));
    expect(send).toHaveBeenCalledWith("new-id", {
      message_id: "new-id",
      text: "空历史时也能提问吗？",
    });
  });

  it("shows the history error only when the history request actually fails", async () => {
    renderPage({ fetchHistory: vi.fn().mockRejectedValue(new Error("network unavailable")) });

    expect(await screen.findByRole("alert", {}, { timeout: 2_500 })).toHaveTextContent("历史对话加载失败。");
  });

  it("selects the latest persisted conversation and renders its safe citations", async () => {
    const fetchTurns = vi.fn().mockResolvedValue(turns);
    renderPage({ fetchTurns });

    await waitFor(() => expect(fetchTurns).toHaveBeenCalledWith("thread-1"));
    await waitFor(() => expect(screen.getAllByText("先明确你要验证的假设。")).toHaveLength(2));
    expect(screen.getByRole("link", { name: /访谈方法/ })).toHaveAttribute("href", "https://example.test/video");
    expect(screen.getByText("1:05")).toBeInTheDocument();
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
    const submit = screen.getByRole("button", { name: "发送问题" });
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

  it("creates a fresh server conversation from the sidebar control", async () => {
    const user = userEvent.setup();
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({ reset });

    await user.click(await screen.findByRole("button", { name: "新建对话" }));
    expect(reset).toHaveBeenCalledWith("new-id");
  });
});
