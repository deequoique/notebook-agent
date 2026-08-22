import Markdown from "react-markdown";

interface MarkdownAnswerProps {
  children: string;
}

export function MarkdownAnswer({ children }: MarkdownAnswerProps) {
  return (
    <div className="chat-markdown">
      <Markdown
        components={{
          a: ({ children: linkText }) => <span>{linkText}</span>,
          img: ({ alt }) => <span>{alt ?? ""}</span>,
        }}
      >
        {children}
      </Markdown>
    </div>
  );
}
