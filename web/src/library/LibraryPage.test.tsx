import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Capabilities, LibraryItem, LibraryPageResponse } from "../api/contracts";
import { LibraryPage } from "./LibraryPage";

const readyItem: LibraryItem = {
  public_id: "video-public",
  platform: "youtube",
  kind: "video",
  url: "https://youtu.be/x",
  title: "理解比收藏重要",
  author: "Notebook Studio",
  published_at: null,
  duration_sec: 60,
  lang: "zh",
  description: null,
  tags: [],
  chapters: [],
  cover_url: null,
  saved_at: "2026-08-07T10:00:00Z",
  why_saved: null,
  text_source: "youtube_captions",
  lifecycle: "ready",
  error_code: null,
  available_actions: ["archive"],
  latest_dispatch_public_id: null,
};

function renderPage(fetchItems: () => Promise<LibraryPageResponse>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const capabilities: Capabilities = {
    supported_platforms: ["youtube"],
    browser_companion: true,
    web_login_channels: ["telegram"],
    save_enabled: true,
    max_save_batch_size: 10,
    transcript_pagination: true,
    archive: true,
    summary_generation: false,
    chat: false,
  };
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LibraryPage
          fetchItems={fetchItems}
          loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
          submitBatch={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("library page", () => {
  it("shows static agent guidance only after a successful true-empty response", async () => {
    renderPage(async () => ({ items: [], total: 0, page: 1, page_size: 20, is_true_first_empty: true }));
    expect(await screen.findByText("资料库还是空的")).toBeInTheDocument();
    expect(screen.queryByText(/我是你的资料整理助手/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加视频" })).toBeInTheDocument();
  });

  it("renders server-owned items instead of an empty agent state", async () => {
    renderPage(async () => ({ items: [readyItem], total: 1, page: 1, page_size: 20, is_true_first_empty: false }));
    expect(await screen.findByText("理解比收藏重要")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
    expect(screen.queryByText("你真正想留下的内容")).not.toBeInTheDocument();
    expect(screen.queryByText(/我是你的资料整理助手/)).not.toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "搜索标题、作者或保存说明" })).toHaveAttribute(
      "name",
      "search",
    );
    expect(screen.getByRole("searchbox", { name: "搜索标题、作者或保存说明" })).toHaveAttribute(
      "autocomplete",
      "off",
    );
    expect(screen.getByRole("button", { name: "搜索" })).toHaveTextContent("搜索");
  });

  it("separates work items below readable videos and shows approximate progress", async () => {
    const processingItem: LibraryItem = {
      ...readyItem,
      public_id: "processing-video",
      title: "正在整理的视频",
      lifecycle: "processing",
    };
    const failedItem: LibraryItem = {
      ...readyItem,
      public_id: "failed-video",
      title: "整理失败的视频",
      lifecycle: "failed",
    };
    renderPage(async () => ({
      items: [processingItem, readyItem, failedItem],
      total: 3,
      page: 1,
      page_size: 20,
      is_true_first_empty: false,
    }));

    const readableRegion = await screen.findByRole("region", { name: "可阅读视频" });
    expect(screen.getByLabelText("当前可阅读视频数量")).toHaveTextContent("1 个视频");
    expect(within(readableRegion).getByText("理解比收藏重要")).toBeInTheDocument();
    expect(within(readableRegion).queryByText("正在整理的视频")).not.toBeInTheDocument();
    expect(within(readableRegion).queryByText("整理失败的视频")).not.toBeInTheDocument();

    const workRegion = screen.getByRole("region", { name: "整理队列" });
    expect(within(workRegion).getByLabelText("整理队列视频数量")).toHaveTextContent("2 个视频");
    expect(within(workRegion).getByText("正在整理的视频")).toBeInTheDocument();
    expect(within(workRegion).getByText("整理失败的视频")).toBeInTheDocument();
    expect(within(workRegion).queryByText("理解比收藏重要")).not.toBeInTheDocument();
    expect(within(workRegion).queryByText("整理完成并可阅读后，会自动移到上方。失败或需要处理的视频会留在这里。")).not.toBeInTheDocument();
    expect(within(workRegion).getByRole("progressbar", { name: "当前整理进度" })).toHaveAttribute("value", "65");
    expect(within(workRegion).getByText("约 65%")).toBeInTheDocument();
  });

  it("shows the server-owned read-only state instead of opening the add flow", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LibraryPage
            fetchItems={vi.fn().mockResolvedValue({
              items: [readyItem], total: 1, page: 1, page_size: 20, is_true_first_empty: false,
            })}
            loadCapabilities={vi.fn().mockResolvedValue({
              supported_platforms: ["youtube"],
              web_login_channels: ["telegram"],
              save_enabled: false,
              max_save_batch_size: 10,
              transcript_pagination: true,
              archive: true,
              summary_generation: false,
              chat: false,
            })}
            submitBatch={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("button", { name: "暂时无法添加视频" })).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("discovers collection tags and sends a dedicated exact collection filter", async () => {
    const allItems = [
      { ...readyItem, public_id: "one", why_saved: "准备用户访谈 #产品调研" },
      { ...readyItem, public_id: "two", why_saved: "复习基础概念 #AI_入门" },
    ];
    const fetchItems = vi.fn().mockImplementation(async (query) => ({
      items: query.collection ? [allItems[0]] : allItems,
      total: query.collection ? 1 : 2,
      page: 1,
      page_size: 20,
      is_true_first_empty: false,
    }));
    const user = userEvent.setup();
    renderPage(fetchItems);

    const filters = await screen.findByRole("navigation", { name: "收藏夹筛选" });
    expect(within(filters).getByRole("button", { name: "产品调研" })).toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: "AI_入门" })).toBeInTheDocument();
    await user.click(within(filters).getByRole("button", { name: "产品调研" }));

    await waitFor(() => expect(fetchItems).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "", collection: "产品调研" }),
    ));
    expect(within(filters).getByRole("button", { name: "产品调研" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(filters).getByRole("button", { name: "AI_入门" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "添加视频" }));
    const dialog = screen.getByRole("dialog", { name: "添加视频链接" });
    expect(within(dialog).getByRole("button", { name: "产品调研" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "AI_入门" })).toBeInTheDocument();
  });

  it("suggests matching loaded titles, authors, and save reasons while typing", async () => {
    const searchableItem = {
      ...readyItem,
      title: "Eric 的用户访谈",
      author: "Eric Migicovsky",
      why_saved: "准备 Eric 访谈 #Eric资料",
    };
    const fetchItems = vi.fn().mockResolvedValue({
      items: [searchableItem],
      total: 1,
      page: 1,
      page_size: 20,
      is_true_first_empty: false,
    });
    const user = userEvent.setup();
    renderPage(fetchItems);

    const searchbox = await screen.findByRole("searchbox", { name: "搜索标题、作者或保存说明" });
    await user.type(searchbox, "Eric");

    const suggestions = screen.getByRole("list", { name: "搜索建议" });
    expect(within(suggestions).getByRole("button", { name: "标题 Eric 的用户访谈" })).toBeInTheDocument();
    expect(within(suggestions).getByRole("button", { name: "作者 Eric Migicovsky" })).toBeInTheDocument();
    expect(within(suggestions).getByRole("button", { name: "收藏夹 #Eric资料" })).toBeInTheDocument();
    expect(within(suggestions).getByRole("button", { name: "保存说明 准备 Eric 访谈" })).toBeInTheDocument();
    expect(fetchItems).toHaveBeenCalledTimes(1);

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("list", { name: "搜索建议" })).not.toBeInTheDocument();
    await user.click(searchbox);

    const reopenedSuggestions = screen.getByRole("list", { name: "搜索建议" });
    const authorSuggestion = within(reopenedSuggestions).getByRole("button", { name: "作者 Eric Migicovsky" });
    const pointerPress = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    fireEvent(authorSuggestion, pointerPress);
    expect(pointerPress.defaultPrevented).toBe(true);
    await user.click(authorSuggestion);
    await waitFor(() => expect(fetchItems).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "Eric Migicovsky" }),
    ));
    expect(searchbox).toHaveValue("Eric Migicovsky");
    expect(screen.queryByRole("list", { name: "搜索建议" })).not.toBeInTheDocument();
  });
});
