import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { ShowcasePage } from "./ShowcasePage";

function renderShowcase() {
  return render(
    <MemoryRouter>
      <ShowcasePage />
    </MemoryRouter>,
  );
}

describe("ShowcasePage", () => {
  it("shows the production ICP filing link", () => {
    render(
      <MemoryRouter>
        <ShowcasePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "粤ICP备2026101890号-1" })).toHaveAttribute(
      "href",
      "https://beian.miit.gov.cn/",
    );
  });

  it("explains the project, audience, workflow, and honest preset-demo boundary", () => {
    const { container } = renderShowcase();

    expect(container.querySelector("[style]")).not.toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "让收藏过的知识，再次可用。" })).toBeInTheDocument();
    expect(screen.getByText("散落在视频中的知识与信息，从此成为你的助手与知识库。"))
      .toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /不要让遗忘成为收藏视频的终点/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/当我们按下收藏键的刹那/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "独立资料空间" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "基于原文生成回答" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "答案依据全程可追溯" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "为了让你跳过等待而设计：“我好像看过这个……我找找？”",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从视频归档，到可追溯的回答。" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "提交并归档视频来源" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "异步解析并建立索引" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "在个人资料库中检索" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "生成带来源依据的回答" })).toBeInTheDocument();
    expect(screen.getByText(/建立可检索的内容索引/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "选一个场景，看答案出处。" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "答案与原文，一步之遥" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "下次需要答案，直接回到原文。" })).not.toBeInTheDocument();
    expect(container.querySelectorAll(".showcase-audience__icon")).toHaveLength(4);
    expect(screen.getByRole("heading", { name: "跨平台收集" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "多渠道使用者" })).not.toBeInTheDocument();
    expect(screen.getByText(/不会调用模型或上传数据/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /进入资料库/ })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "下载浏览器伴侣" })).toHaveAttribute(
      "href",
      "/assets/notebook-agent-browser-companion-production-0.1.3.zip",
    );
    expect(screen.getByLabelText("从视频到可追溯答案的处理路径")).toBeInTheDocument();
    expect(screen.queryByText("来源可核对")).not.toBeInTheDocument();
    expect(container.querySelector(".instrument-status")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".showcase-brand__mark.brand-logo")).toHaveLength(2);
    expect(screen.getAllByRole("img")).toHaveLength(4);
    expect(screen.getByRole("img", { name: "How to Talk to Users 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
    );
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
    );
    expect(screen.getByRole("button", { name: "显示中文要点" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("demo-subtitle-ticker")).toHaveTextContent(/最近一次真实经历/);
    expect(container.querySelector(".demo-subtitle-bar > .sr-only")).toHaveTextContent(/片段要点.*最近一次真实经历/);
    expect(screen.getByLabelText("预设回答演示步骤")).toBeInTheDocument();
    expect(
      screen.queryByText("这组公开视频与时间点已经提前核对。点击下方按钮，查看系统如何从原文整理出答案。"),
    ).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent("视频来源 + 保存说明");
    expect(document.body).toHaveTextContent("字幕与章节 → 内容索引");
    expect(document.body).toHaveTextContent("页面内容、检索结果与回答依据都限定在当前账户范围内");
    expect(document.body).toHaveTextContent("可用入口以部署配置为准");
    expect(document.body).not.toHaveTextContent(/why_saved|tenant|Transcript|Chunks|Hybrid Search|EVIDENCE MODE|混合索引/i);
  });

  it("runs each source-backed preset only after the visitor asks for it", async () => {
    const user = userEvent.setup();
    const { container } = renderShowcase();

    expect(screen.queryByText(/不要先推销方案/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看这次回答/ }));

    expect(screen.getAllByText(/不要先推销方案/)).toHaveLength(2);
    const typedSegments = container.querySelectorAll(".demo-typewriter");
    expect(typedSegments.length).toBeGreaterThan(1);
    expect(typedSegments[0]).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".demo-answer__lead .sr-only")).toHaveTextContent(
      /不要先推销方案/,
    );
    expect(container.querySelector(".demo-answer__mark.brand-logo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /06:07/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=367s",
    );

    await user.click(screen.getByRole("button", { name: /AI 入门/ }));
    expect(screen.queryByText(/784 个输入到 10 个输出/)).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "当前场景：But what is a neural network? 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/aircAruvnKk/hqdefault.jpg",
    );
    await user.click(screen.getByRole("button", { name: "显示英文要点" }));
    expect(screen.getByRole("button", { name: "显示英文要点" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("demo-subtitle-ticker")).toHaveTextContent(/28×28 image as 784 brightness inputs/);
    await user.click(screen.getByRole("button", { name: /查看这次回答/ }));

    expect(screen.getAllByText(/784 个输入到 10 个输出/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: /03:08/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=aircAruvnKk&t=188s",
    );
  });

  it("previews a hero cover on hover and opens its matching demo on click", async () => {
    const user = userEvent.setup();
    renderShowcase();

    const aiCover = screen.getByRole("link", {
      name: "打开 But what is a neural network? 问答场景",
    });

    expect(aiCover).toHaveAttribute("href", "#demo");
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" }))
      .toBeInTheDocument();

    await user.hover(aiCover);
    expect(aiCover).toHaveClass("is-front");
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" }))
      .toBeInTheDocument();

    await user.click(aiCover);
    expect(screen.getByRole("img", { name: "当前场景：But what is a neural network? 视频封面" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI 入门/ })).toHaveAttribute("aria-pressed", "true");
  });
});
