import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  getConversationTurns,
  listConversations,
  deleteConversation,
  resetConversation,
  sendConversationMessage,
} from "../api/client";
import type {
  ConversationHistoryPage,
  ConversationResponse,
  ConversationTurns,
} from "../api/contracts";
import { RouteLink } from "../app/RouteTransition";

interface ChatPageProps {
  fetchHistory?: () => Promise<ConversationHistoryPage>;
  fetchTurns?: (threadId: string) => Promise<ConversationTurns>;
  reset?: (conversationId: string) => Promise<ConversationResponse>;
  send?: (conversationId: string, input: { message_id: string; text: string }) => Promise<ConversationResponse>;
  deleteThread?: (threadId: string) => Promise<void>;
  createId?: () => string;
}

interface NewConversationResult {
  conversationId: string;
  response: ConversationResponse;
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

export function ChatPage({
  fetchHistory = () => listConversations(),
  fetchTurns = getConversationTurns,
  reset = resetConversation,
  send = sendConversationMessage,
  deleteThread = deleteConversation,
  createId = () => crypto.randomUUID(),
}: ChatPageProps) {
  const queryClient = useQueryClient();
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [openMenuThreadId, setOpenMenuThreadId] = useState<string | null>(null);
  const [confirmingThreadId, setConfirmingThreadId] = useState<string | null>(null);
  const attemptedEmptyBootstrap = useRef(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const history = useQuery({
    queryKey: ["conversations"],
    queryFn: fetchHistory,
    retry: 1,
  });
  const transcript = useQuery({
    queryKey: ["conversation", selectedThreadId],
    queryFn: () => fetchTurns(selectedThreadId as string),
    enabled: selectedThreadId !== null,
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
    mutationFn: async ({ conversationId, text }: { conversationId: string; text: string }) => (
      send(conversationId, { message_id: createId(), text })
    ),
    onSuccess: async () => {
      setDraft("");
      setPendingQuestion(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["conversation", selectedThreadId] }),
      ]);
    },
    onError: () => setPendingQuestion(null),
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
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  useEffect(() => {
    const latest = history.data?.items?.[0];
    if (!latest || selectedThreadId) return;
    setSelectedThreadId(latest.thread_id);
    setSelectedConversationId(latest.conversation_id);
  }, [history.data, selectedThreadId]);

  useEffect(() => {
    if (
      !history.data
      || (history.data.items?.length ?? 0) !== 0
      || selectedThreadId
      || newConversation.isPending
      || attemptedEmptyBootstrap.current
    ) return;
    attemptedEmptyBootstrap.current = true;
    newConversation.mutate();
  }, [history.data, newConversation, selectedThreadId]);

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
    setDraft("");
    setPendingQuestion(null);
    setSelectedThreadId(null);
    setSelectedConversationId(null);
    newConversation.mutate();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !selectedConversationId || sendMessage.isPending) return;
    setPendingQuestion(text);
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

  return (
    <section className="chat-page" aria-labelledby="chat-title">
      <header className="chat-heading">
        <div>
          <p className="eyebrow">资料库助手</p>
          <h1 id="chat-title">AI 智能检索</h1>
          <p>围绕你已保存的视频提问，回答会附上可追溯的来源。</p>
        </div>
        <RouteLink className="button button--ghost chat-library-link" to="/library">返回资料库</RouteLink>
      </header>

      <div className="chat-layout">
        <aside className="chat-sidebar" aria-label="历史对话">
          <div id="chat-history">
            <button
              className="button button--primary button--wide"
              type="button"
              disabled={newConversation.isPending}
              onClick={startNewConversation}
            >
              {newConversation.isPending ? "正在新建…" : "新建对话"}
            </button>
            {newConversation.isError ? <p className="chat-error" role="alert">无法新建对话，请重试。</p> : null}
            {deleteConversationMutation.isError ? <p className="chat-error" role="alert">删除会话失败，请重试。</p> : null}
            {history.isPending ? <p className="chat-sidebar__status" aria-live="polite">正在加载历史对话…</p> : null}
            {history.isError ? <div className="chat-sidebar__status" role="alert"><p>历史对话加载失败。</p><button className="text-button" type="button" onClick={() => void history.refetch()}>重试</button></div> : null}
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
                        {item.preview ? <span>{item.preview}</span> : null}
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

        <div className="chat-conversation" aria-busy={transcript.isPending || sendMessage.isPending}>
          <div className="chat-transcript">
            {transcript.isPending || newConversation.isPending ? <p className="chat-empty" aria-live="polite">正在准备对话…</p> : null}
            {transcript.isError ? <div className="chat-empty" role="alert"><p>无法加载这段对话。</p><button className="text-button" type="button" onClick={() => void transcript.refetch()}>重试</button></div> : null}
            {!transcript.isPending && !transcript.isError && selectedThreadId && turns.length === 0 ? <p className="chat-empty">开始提问吧，我会从你的资料库中寻找答案。</p> : null}
            <ol className="chat-turns" aria-label="对话内容">
              {turns.map((turn) => (
                <li key={`${turn.created_at}:${turn.user_text}`} className="chat-turn">
                  <article className="chat-message chat-message--user"><p className="eyebrow">你的问题</p><p>{turn.user_text}</p></article>
                  <article className={`chat-message chat-message--assistant chat-message--${turn.status}`}>
                    <p className="eyebrow">资料库助手</p>
                    {turn.status === "not_found" ? <p className="chat-answer-state">没有在当前资料库中找到足够依据。</p> : null}
                    {turn.status === "failed" ? <p className="chat-answer-state">这次检索未能完成。</p> : null}
                    <p>{turn.assistant_text}</p>
                    {(turn.citations?.length ?? 0) > 0 ? <ul className="chat-citations" aria-label="回答来源">{turn.citations?.map((citation) => (
                      <li key={`${citation.url}:${citation.start_sec ?? ""}`}>
                        <a href={citation.url} target="_blank" rel="noreferrer"><strong>{citation.title}</strong>{startTime(citation.start_sec) ? <span>{startTime(citation.start_sec)}</span> : null}<small>{citation.excerpt}</small></a>
                      </li>
                    ))}</ul> : null}
                  </article>
                </li>
              ))}
              {pendingQuestion ? <li className="chat-turn"><article className="chat-message chat-message--user"><p className="eyebrow">你的问题</p><p>{pendingQuestion}</p></article><article className="chat-message chat-message--assistant"><p className="eyebrow">资料库助手</p><p aria-live="polite">正在检索资料库…</p></article></li> : null}
            </ol>
            {sendMessage.isError ? <p className="chat-error" role="alert">请求未能完成。请检查网络后重新发送。</p> : null}
          </div>
          <form className="chat-compose" onSubmit={submit}>
            <label htmlFor="chat-question">向资料库提问</label>
            <textarea id="chat-question" value={draft} disabled={!selectedConversationId || sendMessage.isPending || newConversation.isPending} onChange={(event) => setDraft(event.target.value)} placeholder="例如：这些视频对用户访谈有哪些建议？" rows={3} />
            <button className="button button--primary" type="submit" disabled={!draft.trim() || !selectedConversationId || sendMessage.isPending || newConversation.isPending}>{sendMessage.isPending ? "正在检索…" : "发送问题"}</button>
          </form>
        </div>
      </div>
    </section>
  );
}
