import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownAnswer } from "./MarkdownAnswer";

describe("MarkdownAnswer", () => {
  it("renders the restrained Markdown structures semantically", () => {
    const { container } = render(
      <MarkdownAnswer>{[
        "## 结论",
        "",
        "第一段包含 **重点** 和 `open_at`。",
        "",
        "- 无序项",
        "1. 有序项",
        "",
        "> 保持问题聚焦。",
      ].join("\n")}</MarkdownAnswer>,
    );

    expect(screen.getByRole("heading", { level: 2, name: "结论" })).toBeInTheDocument();
    expect(screen.getByText("重点").tagName).toBe("STRONG");
    expect(screen.getByText("open_at").tagName).toBe("CODE");
    expect(screen.getByText("无序项").closest("ul")).not.toBeNull();
    expect(screen.getByText("有序项").closest("ol")).not.toBeNull();
    expect(screen.getByText("保持问题聚焦。").closest("blockquote")).not.toBeNull();
    expect(container.querySelector(".chat-markdown")).not.toBeNull();
  });

  it("keeps plain text readable and suppresses active model-authored content", () => {
    const { container } = render(
      <MarkdownAnswer>{[
        "历史纯文本回答。",
        "",
        "[external](https://example.test)",
        "![private image](https://example.test/image.png)",
        "<script>alert('unsafe')</script>",
      ].join("\n")}</MarkdownAnswer>,
    );

    expect(screen.getByText("历史纯文本回答。").tagName).toBe("P");
    expect(screen.getByText("external").tagName).toBe("SPAN");
    expect(screen.getByText("private image").tagName).toBe("SPAN");
    expect(container.querySelector("a, img, script")).toBeNull();
    expect(container).toHaveTextContent("<script>alert('unsafe')</script>");
  });
});
