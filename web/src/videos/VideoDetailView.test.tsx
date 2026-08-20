import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LibraryItem, TranscriptPage } from "../api/contracts";
import { VideoDetailView } from "./VideoDetailView";

const item: LibraryItem = {
  public_id: "video-public",
  platform: "youtube",
  kind: "video",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "如何把信息变成理解",
  author: "Notebook Studio",
  published_at: "2026-08-01T00:00:00Z",
  duration_sec: 620,
  lang: "zh",
  description: "原视频作者提供的简介，不是 AI 摘要。",
  tags: [],
  chapters: [{ title: "重新定义记录", start_sec: 0 }, { title: "建立联系", start_sec: 185 }],
  cover_url: null,
  saved_at: "2026-08-07T10:00:00Z",
  why_saved: "整理知识管理方法",
  text_source: "youtube_captions",
  lifecycle: "ready",
  error_code: null,
  available_actions: ["archive", "edit_why_saved", "open_source"],
  latest_dispatch_public_id: "dispatch-public",
};

const transcript: TranscriptPage = {
  blocks: [
    { ordinal: 0, start_sec: 0, end_sec: 10, text: "记录不是终点，理解才是。", source_url: "https://youtu.be/x?t=0" },
    { ordinal: 1, start_sec: 10, end_sec: 22, text: "先把材料放进自己的问题里。", source_url: "https://youtu.be/x?t=10" },
  ],
  next_cursor: "next-page",
};

describe("video detail", () => {
  it("keeps a long title complete while opting into compact hero typography", () => {
    const longTitle = "How to practice effectively...for just about anything";
    render(
      <VideoDetailView
        item={{ ...item, title: longTitle, cover_url: "https://i.ytimg.com/vi/example/hqdefault.jpg" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    const heading = screen.getByRole("heading", { name: longTitle });
    expect(heading).toHaveTextContent(longTitle);
    expect(heading).toHaveAttribute("data-title-density", "compact");
  });

  it("shows chapters and original transcript without pretending description is a summary", () => {
    const { container } = render(
      <VideoDetailView
        item={{ ...item, cover_url: "https://i.ytimg.com/vi/example/hqdefault.jpg" }}
        transcriptPages={[transcript]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "章节" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "字幕节选" })).toBeInTheDocument();
    expect(screen.getByText("根据原视频字幕整理")).toBeInTheDocument();
    expect(screen.getByText("点击时间跳到原视频对应位置")).toBeInTheDocument();
    expect(screen.queryByText(/搜索切片/)).not.toBeInTheDocument();
    expect(screen.getByText("记录不是终点，理解才是。")).toBeInTheDocument();
    expect(screen.getByText("原视频作者提供的简介，不是 AI 摘要。")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /摘要/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续加载字幕" })).toBeInTheDocument();
    expect(screen.getByText("中文")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("width", "960");
    expect(container.querySelector("img")).toHaveAttribute("height", "540");
    expect(container.querySelector("img")).toHaveAttribute("fetchpriority", "high");
  });

  it("makes overflowing chapters keyboard-scrollable", () => {
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("list", { name: "视频章节" })).toHaveAttribute("tabindex", "0");
  });

  it("keeps source-provided transcript timestamps inside a keyboard-scrollable reader", () => {
    const sentenceTranscript: TranscriptPage = {
      blocks: [
        {
          ordinal: 0,
          start_sec: 60,
          end_sec: 70,
          text: "先问最近一次真实经历。再追问当时采用了什么办法！",
          source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=60",
        },
      ],
      next_cursor: null,
    };
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[sentenceTranscript]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    const reader = screen.getByRole("region", { name: "字幕节选，可滚动查看" });
    expect(reader).toHaveAttribute("tabindex", "0");
    expect(within(reader).getAllByRole("listitem")).toHaveLength(1);
    expect(within(reader).getByText("先问最近一次真实经历。再追问当时采用了什么办法！")).toBeInTheDocument();
    expect(within(reader).getByRole("link", { name: "从 1:00 播放" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=60",
    );
    expect(screen.queryByRole("link", { name: "从 1:05 播放" })).not.toBeInTheDocument();
    expect(screen.getByText("节选 1 段")).toBeInTheDocument();
  });

  it("renders an existing non-empty backend summary without generating one", () => {
    render(
      <VideoDetailView
        item={{ ...item, summary: "这是此前已经存储的摘要。" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "摘要" })).toBeInTheDocument();
    expect(screen.getByText("这是此前已经存储的摘要。")).toBeInTheDocument();
  });

  it("exposes retry only when the backend lists it", () => {
    const { rerender } = render(
      <VideoDetailView
        item={{ ...item, lifecycle: "failed", available_actions: ["retry", "archive"] }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "重新整理" })).toBeInTheDocument();

    rerender(
      <VideoDetailView
        item={{ ...item, lifecycle: "processing", available_actions: ["archive"] }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
  });

  it("distinguishes transcript and action failures from genuinely empty content", () => {
    const retryTranscript = vi.fn();
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        transcriptError
        actionError
        onLoadMore={vi.fn()}
        onRetryTranscript={retryTranscript}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert", { name: "视频操作失败" })).toHaveTextContent(
      "操作未完成",
    );
    expect(screen.getByRole("alert", { name: "字幕加载失败" })).toHaveTextContent(
      "字幕暂时无法加载",
    );
    expect(screen.queryByText("这段视频没有可显示的字幕。")).not.toBeInTheDocument();
    screen.getByRole("button", { name: "重新加载字幕" }).click();
    expect(retryTranscript).toHaveBeenCalledOnce();
  });

  it("shows a loading state before deciding that a ready transcript is empty", () => {
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        transcriptInitialPending
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByText("正在加载字幕…")).toBeInTheDocument();
    expect(screen.queryByText("这段视频没有可显示的字幕。")).not.toBeInTheDocument();
  });

  it("closes why-saved editing only after the update succeeds", async () => {
    const user = userEvent.setup();
    const updateWhySaved = vi
      .fn()
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce(undefined);
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={updateWhySaved}
      />,
    );

    await user.click(screen.getByRole("button", { name: "编辑说明和收藏夹" }));
    const input = screen.getByRole("textbox", { name: "保存说明" });
    await user.clear(input);
    await user.type(input, "新的保存原因");
    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));
    expect(await screen.findByRole("textbox", { name: "保存说明" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));
    expect(screen.queryByRole("textbox", { name: "保存说明" })).not.toBeInTheDocument();
    expect(updateWhySaved).toHaveBeenCalledTimes(2);
  });

  it("shows collection tags separately and lets the saved context editor add a collection", async () => {
    const user = userEvent.setup();
    const updateWhySaved = vi.fn().mockResolvedValue(undefined);
    render(
      <VideoDetailView
        item={{ ...item, why_saved: "整理知识管理方法 #产品调研" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={updateWhySaved}
      />,
    );

    expect(screen.getByLabelText("所属收藏夹")).toHaveTextContent("#产品调研");
    expect(screen.getByText("整理知识管理方法")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑说明和收藏夹" }));
    const reason = screen.getByRole("textbox", { name: "保存说明" });
    expect(reason).toHaveValue("整理知识管理方法");
    await user.clear(reason);
    await user.type(reason, "新的保存说明");
    await user.type(screen.getByRole("textbox", { name: "新收藏夹" }), "AI_入门");
    await user.click(screen.getByRole("button", { name: "添加" }));
    expect(screen.getByRole("list", { name: "正在编辑的收藏夹" })).toHaveTextContent("#AI_入门");
    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));

    expect(updateWhySaved).toHaveBeenCalledWith("新的保存说明 #产品调研 #AI_入门");
  });

  it("maps language codes and cover placeholders to user-facing copy", () => {
    const { rerender } = render(
      <VideoDetailView
        item={{ ...item, lang: "zh-Hans", cover_url: null }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByText("简体中文")).toBeInTheDocument();
    expect(screen.getByText("暂无封面")).toBeInTheDocument();
    expect(screen.queryByText(/ZH-HANS|YT/)).not.toBeInTheDocument();

    rerender(
      <VideoDetailView
        item={{ ...item, lang: "x-private", cover_url: null }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.queryByText(/X-PRIVATE/)).not.toBeInTheDocument();
  });
});
