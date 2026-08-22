import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

import {
  getCapabilities,
  ConversationStreamError,
  getConversationTurns,
  listConversations,
  deleteConversation,
  resetConversation,
  sendConversationMessage,
  StreamingUnavailableError,
} from "../api/client";
import type {
  Capabilities,
  ConversationCitation,
  ConversationHistoryPage,
  ConversationResponse,
  ConversationStreamEvent,
  ConversationTurns,
} from "../api/contracts";
import { ApiError } from "../api/client";
import { RouteLink } from "../app/RouteTransition";
import { MarkdownAnswer } from "./MarkdownAnswer";

interface ChatPageProps {
  loadCapabilities?: () => Promise<Capabilities>;
  fetchHistory?: () => Promise<ConversationHistoryPage>;
  fetchTurns?: (threadId: string) => Promise<ConversationTurns>;
  reset?: (conversationId: string) => Promise<ConversationResponse>;
  send?: (conversationId: string, input: { message_id: string; text: string }) => Promise<ConversationResponse>;
  sendStream?: (
    conversationId: string,
    input: { message_id: string; text: string },
    onEvent: (event: ConversationStreamEvent) => void,
  ) => Promise<ConversationResponse>;
  deleteThread?: (threadId: string) => Promise<void>;
  createId?: () => string;
}

function sendErrorMessage(error: Error | null): string {
  if (error instanceof ApiError && error.status === 504) {
    return "回答生成超时，请重新发送。";
  }
  return "请求未能完成。请检查网络后重新发送。";
}

interface NewConversationResult {
  conversationId: string;
  response: ConversationResponse;
}

interface PendingSection {
  sectionId: string;
  status: "grounded" | "unsupported";
  text: string;
  citations: ConversationCitation[];
  phase: "streaming" | "completed";
}

function displayTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", {
    month: "short", day: "numeric",
  });
}

function startTime(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function assistantTextWithoutSourceList(text: string, hasCitations: boolean): string {
  if (!hasCitations) return text;
  const sourceListStart = text.lastIndexOf("\n\n来源：\n");
  return sourceListStart === -1 ? text : text.slice(0, sourceListStart).trimEnd();
}

function markdownPreviewText(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\s*\[S\d+\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const fallbackQuestions = [
  "这个观点在哪个视频的什么位置？",
  "这些视频对这个问题有哪些直接依据？",
  "请列出相关片段和可跳转的时间点。",
];

const ACTIVITY_COPY: Record<string, string> = {
  preparing: "正在准备回答…",
  retrieving: "正在检索资料库…",
  planning_answer: "正在规划回答依据…",
  composing: "正在整理答案…",
  completed: "回答已完成",
  failed: "这次检索未能完成",
  cancelled: "请求已取消",
};

function activityCopy(event: ConversationStreamEvent): string {
  return event.activity ? (ACTIVITY_COPY[event.activity] ?? "正在处理…") : "正在处理…";
}

function CitationList({ citations }: { citations: ConversationCitation[] }) {
  if (!citations.length) return null;
  return (
    <ol className="chat-citations">
      {citations.map((citation, citationIndex) => (
        <li key={`${citation.url}:${citation.start_sec ?? ""}:${citationIndex}`}>
          <a href={citation.url} target="_blank" rel="noreferrer">
            <span className="chat-citation-index">{String(citationIndex + 1).padStart(2, "0")}</span>
            <span className="chat-citation-copy"><strong>{citation.title}</strong></span>
            {startTime(citation.start_sec) ? <span className="chat-citation-time">{startTime(citation.start_sec)} ↗</span> : <span className="chat-citation-time">打开 ↗</span>}
          </a>
          <details className="chat-citation-excerpt">
            <summary>展开字幕依据</summary>
            <p>{citation.excerpt}</p>
          </details>
        </li>
      ))}
    </ol>
  );
}

export function ChatPage({
  loadCapabilities = getCapabilities,
  fetchHistory = () => listConversations(),
  fetchTurns = getConversationTurns,
  reset = resetConversation,
  send = sendConversationMessage,
  sendStream,
  deleteThread = deleteConversation,
  createId = () => crypto.randomUUID(),
}: ChatPageProps) {
  const queryClient = useQueryClient();
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [pendingActivity, setPendingActivity] = useState("正在准备回答…");
  const [pendingAnswer, setPendingAnswer] = useState("");
  const [pendingCitations, setPendingCitations] = useState<ConversationCitation[]>([]);
  const [pendingSections, setPendingSections] = useState<PendingSection[]>([]);
  const [pendingStatus, setPendingStatus] = useState<"streaming" | "failed">("streaming");
  const [openMenuThreadId, setOpenMenuThreadId] = useState<string | null>(null);
  const [confirmingThreadId, setConfirmingThreadId] = useState<string | null>(null);
  const attemptedEmptyBootstrap = useRef(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: loadCapabilities,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const chatEnabled = capabilities.data?.chat === true;
  const history = useQuery({
    queryKey: ["conversations"],
    queryFn: fetchHistory,
    enabled: chatEnabled,
    retry: 1,
  });
  const transcript = useQuery({
    queryKey: ["conversation", selectedThreadId],
    queryFn: () => fetchTurns(selectedThreadId as string),
    enabled: chatEnabled && selectedThreadId !== null,
    retry: 1,
  });
  const newConversation = useMutation({
    mutationFn: async (): Promise<NewConversationResult> => {
      const conversationId = createId();
      const response = await reset(conversationId);
      if (!response.thread_id) throw new Error("服务器未返回新会话。");
      return { conversationId, response };
    },
    onSuccess: async ({ conversationId, response }) => {
      setSelectedThreadId(response.thread_id as string);
      // A reset response has the public thread ID but not the browser
      // conversation ID. Keep the ID generated for this request so an empty
      // history can accept a question before the sidebar refresh completes.
      setSelectedConversationId(conversationId);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const sendMessage = useMutation({
    mutationFn: async ({ conversationId, text }: { conversationId: string; text: string }) => {
      const input = { message_id: createId(), text };
      if (!sendStream) return send(conversationId, input);
      try {
        return await sendStream(conversationId, input, (event) => {
          // A fetch reader can deliver multiple complete SSE records in one
          // microtask. Flush each public event at the boundary so activity
          // and the first safe delta are paintable before the terminal
          // response, instead of being collapsed into one React batch.
          flushSync(() => {
            if (event.type === "started" || event.type === "activity") {
              setPendingActivity(activityCopy(event));
            } else if (event.type === "section_started" && event.section_id) {
              setPendingSections((current) => [
                ...current.filter((section) => section.sectionId !== event.section_id),
                {
                  sectionId: event.section_id as string,
                  status: event.status ?? "grounded",
                  text: "",
                  citations: event.citations ?? [],
                  phase: "streaming",
                },
              ]);
              setPendingActivity(
                event.status === "unsupported" ? "正在标记证据不足部分…" : "正在整理答案…",
              );
            } else if (event.type === "text_delta") {
              setPendingActivity("正在整理答案…");
              if (event.text && event.section_id) {
                setPendingSections((current) => current.map((section) => (
                  section.sectionId === event.section_id
                    ? { ...section, text: section.text + event.text }
                    : section
                )));
              } else if (event.text) {
                setPendingAnswer((current) => current + event.text);
              }
            } else if (event.type === "section_completed" && event.section_id) {
              setPendingSections((current) => current.map((section) => (
                section.sectionId === event.section_id
                  ? { ...section, phase: "completed" }
                  : section
              )));
            } else if (event.type === "section_aborted" && event.section_id) {
              setPendingSections((current) => current.filter((section) => section.sectionId !== event.section_id));
            } else if (event.type === "completed") {
              setPendingActivity("回答已完成");
              setPendingSections([]);
              if (event.response) {
                setPendingAnswer(event.response.text);
                setPendingCitations(event.response.citations ?? []);
              }
            } else if (event.type === "error" || event.type === "cancelled") {
              setPendingStatus("failed");
              setPendingActivity(activityCopy(event));
              setPendingAnswer(
                event.response?.text
                  || (event.type === "cancelled" ? "请求已取消。" : "请求未能完成，请稍后重试。"),
              );
              setPendingSections([]);
            }
          });
        });
      } catch (error) {
        // A known server without SSE support is the only safe automatic
        // fallback. A broken stream may already have persisted this message,
        // so it remains a visible failed turn instead of being resubmitted.
        if (error instanceof StreamingUnavailableError) {
          setPendingActivity("正在检索资料库…");
          setPendingAnswer("");
          return send(conversationId, input);
        }
        throw error;
      }
    },
    onSuccess: async () => {
      setDraft("");
      setPendingQuestion(null);
      setPendingAnswer("");
      setPendingCitations([]);
      setPendingSections([]);
      setPendingActivity("正在准备回答…");
      setPendingStatus("streaming");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation", selectedThreadId] }),
      ]);
    },
    onError: (error) => {
      if (error instanceof ConversationStreamError) {
        setPendingStatus("failed");
        setPendingSections([]);
        setPendingCitations([]);
        setPendingActivity(
          error.code === "cancelled"
            ? "请求已取消"
            : error.response
              ? "这次检索未能完成"
              : "流式连接中断，请重试",
        );
        if (error.response?.text) setPendingAnswer(error.response.text);
        return;
      }
      setPendingQuestion(null);
      setPendingAnswer("");
      setPendingCitations([]);
      setPendingSections([]);
      setPendingStatus("failed");
    },
  });
  const deleteConversationMutation = useMutation({
    mutationFn: async (threadId: string) => {
      await deleteThread(threadId);
      return threadId;
    },
    onSuccess: async (deletedThreadId) => {
      queryClient.setQueryData<ConversationHistoryPage>(["conversations"], (current) => {
        if (!current) return current;
        return {
          ...current,
          items: (current.items ?? []).filter((item) => item.thread_id !== deletedThreadId),
        };
      });
      setSelectedThreadId(null);
      setSelectedConversationId(null);
      setPendingQuestion(null);
      setPendingAnswer("");
      setPendingActivity("正在准备回答…");
      setPendingStatus("streaming");
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  useEffect(() => {
    const latest = history.data?.items?.[0];
    if (!chatEnabled || !latest || selectedThreadId) return;
    setSelectedThreadId(latest.thread_id);
    setSelectedConversationId(latest.conversation_id);
  }, [chatEnabled, history.data, selectedThreadId]);

  useEffect(() => {
    if (
      !chatEnabled
      || !history.data
      || (history.data.items?.length ?? 0) !== 0
      || selectedThreadId
      || newConversation.isPending
      || attemptedEmptyBootstrap.current
    ) return;
    attemptedEmptyBootstrap.current = true;
    newConversation.mutate();
  }, [chatEnabled, history.data, newConversation, selectedThreadId]);

  useEffect(() => {
    if (!selectedThreadId || selectedConversationId || !history.data) return;
    const item = history.data.items?.find((entry) => entry.thread_id === selectedThreadId);
    if (item) setSelectedConversationId(item.conversation_id);
  }, [history.data, selectedConversationId, selectedThreadId]);

  useEffect(() => {
    if (!openMenuThreadId) return;
    function closeMenu(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpenMenuThreadId(null);
        setConfirmingThreadId(null);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpenMenuThreadId(null);
        setConfirmingThreadId(null);
      }
    }
    document.addEventListener("pointerdown", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openMenuThreadId]);

  function selectConversation(threadId: string, conversationId: string) {
    setSelectedThreadId(threadId);
    setSelectedConversationId(conversationId);
  }

  function startNewConversation() {
    if (!chatEnabled) return;
    setDraft("");
    setPendingQuestion(null);
    setPendingAnswer("");
    setPendingCitations([]);
    setPendingSections([]);
    setPendingActivity("正在准备回答…");
    setPendingStatus("streaming");
    setSelectedThreadId(null);
    setSelectedConversationId(null);
    newConversation.mutate();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !selectedConversationId || sendMessage.isPending) return;
    setPendingQuestion(text);
    setPendingAnswer("");
    setPendingCitations([]);
    setPendingSections([]);
    setPendingActivity("正在准备回答…");
    setPendingStatus("streaming");
    sendMessage.mutate({ conversationId: selectedConversationId, text });
  }

  function deleteSelectedConversation(threadId: string) {
    if (deleteConversationMutation.isPending) return;
    setOpenMenuThreadId(null);
    setConfirmingThreadId(null);
    deleteConversationMutation.mutate(threadId);
  }

  const turns = transcript.data?.turns ?? [];
  const historyItems = history.data?.items ?? [];
  const suggestedQuestions = Array.from(new Set([
    ...historyItems
      .filter((item) => item.thread_id !== selectedThreadId && item.title !== "新的检索")
      .map((item) => item.title.trim())
      .filter(Boolean),
    ...fallbackQuestions,
  ])).slice(0, 3);
  const conversationBusy = capabilities.isPending
    || newConversation.isPending
    || sendMessage.isPending
    || (selectedThreadId !== null && transcript.isPending);

  return (
    <section className="chat-page" aria-labelledby="chat-title">
      <header className="chat-heading">
        <div className="chat-heading__copy">
          <RouteLink className="chat-back-link" to="/library">← 返回资料库</RouteLink>
          <p className="eyebrow">资料库检索</p>
          <h1 id="chat-title">AI 智能检索</h1>
          <p>从已保存的视频中查找答案，并返回可跳转的原视频依据。</p>
        </div>
        <div className="chat-scope" aria-label="检索范围">
          <span><i aria-hidden="true" />当前资料库</span>
          <strong>仅依据已保存视频</strong>
          <small>回答附带原视频与时间点</small>
        </div>
      </header>

      <div className="chat-layout">
        <aside className="chat-sidebar" aria-label="历史对话">
          <div id="chat-history">
            <div className="chat-sidebar__heading">
              <div>
                <p className="eyebrow">历史检索</p>
                <span>{historyItems.length > 0 ? `最近 ${historyItems.length} 段` : "当前账户"}</span>
              </div>
              <button
                className="chat-new-thread"
                type="button"
                aria-label="新建检索"
                disabled={!chatEnabled || newConversation.isPending}
                onClick={startNewConversation}
              >
                {capabilities.isPending || newConversation.isPending ? "…" : "+"}
              </button>
            </div>
            {capabilities.isPending ? <p className="chat-sidebar__status" aria-live="polite">正在确认检索能力…</p> : null}
            {capabilities.isError ? <div className="chat-sidebar__status"><p>无法确认检索服务状态。</p><button className="text-button" type="button" onClick={() => void capabilities.refetch()}>重试</button></div> : null}
            {capabilities.isSuccess && !chatEnabled ? <p className="chat-sidebar__status">当前部署未开放 AI 检索。</p> : null}
            {newConversation.isError ? <p className="chat-error" role="alert">无法新建对话，请重试。</p> : null}
            {deleteConversationMutation.isError ? <p className="chat-error" role="alert">删除会话失败，请重试。</p> : null}
            {chatEnabled && history.isPending ? <p className="chat-sidebar__status" aria-live="polite">正在加载历史对话…</p> : null}
            {history.isError ? <div className="chat-sidebar__status"><p>历史对话加载失败。</p><button className="text-button" type="button" onClick={() => void history.refetch()}>重试</button></div> : null}
            {history.isSuccess && historyItems.length === 0 && !newConversation.isPending && !newConversation.isError ? <p className="chat-sidebar__status">暂无历史对话。</p> : null}
            {historyItems.length > 0 ? (
              <nav aria-label="选择历史对话">
                <ol className="chat-thread-list">
                  {historyItems.map((item) => (
                    <li key={item.thread_id} className="chat-thread-row">
                      <button
                        className={`chat-thread-select${item.thread_id === selectedThreadId ? " is-selected" : ""}`}
                        type="button"
                        aria-current={item.thread_id === selectedThreadId ? "page" : undefined}
                        onClick={() => selectConversation(item.thread_id, item.conversation_id)}
                      >
                        <strong>{item.title}</strong>
                        {item.preview ? <span>{markdownPreviewText(item.preview)}</span> : null}
                        <time dateTime={item.updated_at}>{displayTime(item.updated_at)}</time>
                      </button>
                      <div
                        className="chat-thread-menu"
                        ref={openMenuThreadId === item.thread_id ? menuRef : undefined}
                      >
                        <button
                          className="chat-thread-menu__trigger"
                          type="button"
                          aria-label={`“${item.title}”的更多操作`}
                          aria-expanded={openMenuThreadId === item.thread_id}
                          onClick={() => {
                            const opening = openMenuThreadId !== item.thread_id;
                            setOpenMenuThreadId(opening ? item.thread_id : null);
                            setConfirmingThreadId(null);
                          }}
                        >
                          •••
                        </button>
                        {openMenuThreadId === item.thread_id ? (
                          <div className="chat-thread-menu__panel">
                            {confirmingThreadId === item.thread_id ? (
                              <div className="chat-thread-menu__confirm" role="alertdialog" aria-label="确认删除会话">
                                <p>删除这段对话？</p>
                                <div>
                                  <button
                                    className="chat-thread-menu__cancel"
                                    type="button"
                                    onClick={() => setConfirmingThreadId(null)}
                                  >
                                    取消
                                  </button>
                                  <button
                                    className="chat-thread-menu__delete"
                                    type="button"
                                    disabled={deleteConversationMutation.isPending}
                                    onClick={() => deleteSelectedConversation(item.thread_id)}
                                  >
                                    确定删除
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <button
                                className="chat-thread-menu__delete"
                                type="button"
                                disabled={deleteConversationMutation.isPending}
                                onClick={() => setConfirmingThreadId(item.thread_id)}
                              >
                                删除会话
                              </button>
                            )}
                          </div>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              </nav>
            ) : null}
          </div>
        </aside>

        <div className="chat-conversation" aria-busy={conversationBusy}>
          <div className="chat-transcript">
            {capabilities.isPending ? <div className="chat-empty-stage chat-empty-stage--loading" aria-live="polite"><span className="chat-search-line" aria-hidden="true" /><p>正在确认资料库检索能力…</p></div> : null}
            {capabilities.isError ? <div className="chat-empty-stage" role="alert"><p className="eyebrow">检索暂时不可用</p><h2>无法确认当前服务能力</h2><p>请重试；已经保存的视频不会受到影响。</p></div> : null}
            {capabilities.isSuccess && !chatEnabled ? <div className="chat-empty-stage" role="status"><p className="eyebrow">当前部署</p><h2>尚未开放 AI 智能检索</h2><p>你仍可返回资料库阅读视频和字幕。</p></div> : null}
            {(selectedThreadId && transcript.isPending) || newConversation.isPending ? <div className="chat-empty-stage chat-empty-stage--loading" aria-live="polite"><span className="chat-search-line" aria-hidden="true" /><p>正在准备检索工作区…</p></div> : null}
            {transcript.isError ? <div className="chat-empty" role="alert"><p>无法加载这段对话。</p><button className="text-button" type="button" onClick={() => void transcript.refetch()}>重试</button></div> : null}
            {!selectedThreadId && history.isError ? <div className="chat-empty-stage" role="alert"><p className="eyebrow">检索暂时不可用</p><h2>无法连接到资料库检索服务</h2><p>请稍后重试历史记录，已经保存的视频不会受到影响。</p></div> : null}
            {chatEnabled && !transcript.isPending && !transcript.isError && selectedThreadId && turns.length === 0 ? (
              <div className="chat-empty-stage">
                <p className="eyebrow">从一个具体问题开始</p>
                <h2>你想从这些视频里找回什么？</h2>
                <p>问题越具体，越容易定位到对应视频片段和时间点。</p>
                <div className="chat-suggestions" aria-label="示例问题">
                  {suggestedQuestions.map((question, index) => (
                    <button key={question} type="button" onClick={() => setDraft(question)}>
                      <span>{String(index + 1).padStart(2, "0")}</span>{question}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            <ol className="chat-turns" aria-label="对话内容">
              {turns.map((turn, turnIndex) => (
                <li key={`${turn.created_at}:${turn.user_text}`} className="chat-turn">
                  <article className="chat-message chat-message--user">
                    <header><p className="eyebrow">问题 {String(turnIndex + 1).padStart(2, "0")}</p><time dateTime={turn.created_at}>{displayTime(turn.created_at)}</time></header>
                    <p className="chat-question-text">{turn.user_text}</p>
                  </article>
                  <article className={`chat-message chat-message--assistant chat-message--${turn.status}`}>
                    <header className="chat-answer-heading"><p className="eyebrow"><i aria-hidden="true" />资料库回答</p>{(turn.citations?.length ?? 0) > 0 ? <span>依据 {turn.citations?.length}</span> : null}</header>
                    {turn.status === "not_found" ? <p className="chat-answer-state">没有在当前资料库中找到足够依据。</p> : null}
                    {turn.status === "failed" ? <p className="chat-answer-state">这次检索未能完成。</p> : null}
                    <MarkdownAnswer>{assistantTextWithoutSourceList(turn.assistant_text, (turn.citations?.length ?? 0) > 0)}</MarkdownAnswer>
                    {(turn.citations?.length ?? 0) > 0 ? (
                      <section className="chat-evidence" aria-label="回答来源">
                        <div className="chat-evidence__heading"><p>依据 {turn.citations?.length}</p><span>可跳转原视频，字幕默认折叠</span></div>
                        <CitationList citations={turn.citations ?? []} />
                      </section>
                    ) : null}
                  </article>
                </li>
              ))}
              {pendingQuestion ? (
                <li className="chat-turn">
                  <article className="chat-message chat-message--user"><p className="eyebrow">你的问题</p><p>{pendingQuestion}</p></article>
                  <article className={`chat-message chat-message--assistant chat-message--${pendingStatus}`}>
                    <p className="eyebrow">资料库助手</p>
                    <p aria-live="polite" role={pendingStatus === "failed" ? "alert" : "status"}>{pendingActivity}</p>
                    {pendingSections.map((section) => (
                      <section className="chat-pending-section" key={section.sectionId} aria-label={section.status === "unsupported" ? "证据不足部分" : "正在生成回答部分"}>
                        <MarkdownAnswer>{section.text}</MarkdownAnswer>
                        <CitationList citations={section.citations} />
                      </section>
                    ))}
                    {pendingAnswer ? <MarkdownAnswer>{pendingAnswer}</MarkdownAnswer> : null}
                    <CitationList citations={pendingCitations} />
                  </article>
                </li>
              ) : null}
            </ol>
            {sendMessage.isError ? <p className="chat-error" role="alert">{sendErrorMessage(sendMessage.error)}</p> : null}
          </div>
          <form className="chat-compose" onSubmit={submit}>
            <label className="sr-only" htmlFor="chat-question">向资料库提问</label>
            <div className="chat-compose__field">
              <textarea id="chat-question" value={draft} disabled={!selectedConversationId || sendMessage.isPending || newConversation.isPending} onChange={(event) => setDraft(event.target.value)} placeholder="向资料库提问…" rows={2} />
              <div className="chat-compose__footer">
                <span>仅从你的资料库中检索，回答会附上原文依据</span>
                <button className="button button--primary" type="submit" disabled={!draft.trim() || !selectedConversationId || sendMessage.isPending || newConversation.isPending}>{sendMessage.isPending ? "正在检索…" : "发送问题 →"}</button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
