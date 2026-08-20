import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  getCapabilities,
  listLibraryItems,
  submitVideoBatch,
  type LibraryQuery,
} from "../api/client";
import type { BatchSubmitInput, BatchSubmitResponse, Capabilities, LibraryPageResponse } from "../api/contracts";
import { AddVideosDialog } from "./AddVideosDialog";
import { collectCollectionNames } from "./collections";
import { LibraryEmptyState, LibraryErrorState, LibraryLoadingState } from "./LibraryStates";
import { estimateWorkItemProgress, isLibraryWorkItem, shouldPollLibrary } from "./lifecycle";
import { collectSearchSuggestions } from "./searchSuggestions";
import { VideoCard } from "./VideoCard";
import { RouteLink } from "../app/RouteTransition";

interface LibraryPageProps {
  fetchItems?: (query: LibraryQuery) => Promise<LibraryPageResponse>;
  loadCapabilities?: () => Promise<Capabilities>;
  submitBatch?: (input: BatchSubmitInput) => Promise<BatchSubmitResponse>;
}

function libraryErrorDescription(error: unknown): string {
  if (!(error instanceof ApiError)) return "网络连接没有完成，请确认服务可用后重新加载。";
  if (error.status === 403) return "当前账户无法访问这份资料库，请重新登录或联系管理员确认权限。";
  if (error.status === 429) return "请求过于频繁，请稍等片刻后重新加载。";
  if (error.status >= 500) return "资料库服务暂时没有响应，已有内容不会丢失，请稍后重试。";
  return "资料库请求没有完成，请重新加载。";
}

export function LibraryPage({
  fetchItems = listLibraryItems,
  loadCapabilities = getCapabilities,
  submitBatch = submitVideoBatch,
}: LibraryPageProps) {
  const queryClient = useQueryClient();
  const searchFormRef = useRef<HTMLFormElement>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [knownCollections, setKnownCollections] = useState<string[]>([]);
  const [lifecycle, setLifecycle] = useState("");
  const [sort, setSort] = useState<LibraryQuery["sort"]>("saved_desc");
  const [page, setPage] = useState(1);
  const query: LibraryQuery = {
    search,
    collection: selectedCollection ?? undefined,
    lifecycle,
    include_archived: lifecycle === "archived",
    sort,
    page,
    page_size: 20,
  };
  const library = useQuery({
    queryKey: ["library", query],
    queryFn: () => fetchItems(query),
    retry: 1,
    refetchInterval: (state) => shouldPollLibrary(state.state.data?.items ?? []) ? 4_000 : false,
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: loadCapabilities,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const saveDisabled = capabilities.data?.save_enabled !== true;
  const saveNoticeId = capabilities.isError || capabilities.data?.save_enabled === false
    ? "library-save-notice"
    : undefined;
  const saveButtonLabel = capabilities.isPending
    ? "正在确认添加功能"
    : capabilities.isError
      ? "无法确认添加功能"
      : saveDisabled
        ? "暂时无法添加视频"
        : "添加视频";
  useEffect(() => {
    if (!library.data) return;
    const discovered = collectCollectionNames(library.data.items.map((item) => item.why_saved));
    setKnownCollections((current) => {
      const next = collectCollectionNames([
        ...current.map((name) => `#${name}`),
        ...discovered.map((name) => `#${name}`),
      ]);
      return next.length === current.length && next.every((name, index) => name === current[index])
        ? current
        : next;
    });
  }, [library.data]);
  useEffect(() => {
    if (!suggestionsOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !searchFormRef.current?.contains(event.target)) {
        setSuggestionsOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [suggestionsOpen]);
  const collectionOptions = collectCollectionNames([
    ...knownCollections.map((name) => `#${name}`),
    selectedCollection ? `#${selectedCollection}` : null,
  ]);
  const totalPages = library.data ? Math.max(1, Math.ceil(library.data.total / library.data.page_size)) : 1;
  const readableItems = library.data?.items.filter((item) => !isLibraryWorkItem(item)) ?? [];
  const workItems = library.data?.items.filter(isLibraryWorkItem) ?? [];
  const activeWorkItems = workItems.filter(
    ({ lifecycle: state }) => state === "queued" || state === "processing",
  );
  const workProgress = estimateWorkItemProgress(workItems);
  const searchSuggestions = collectSearchSuggestions(library.data?.items ?? [], searchDraft);

  function submitSearch(value: string) {
    const nextSearch = value.trim();
    setSelectedCollection(null);
    setSearchDraft(nextSearch);
    setSearch(nextSearch);
    setSuggestionsOpen(false);
    setPage(1);
  }

  function changeFilter(next: string) {
    setLifecycle(next);
    setPage(1);
  }

  function selectCollection(name: string | null) {
    setSelectedCollection(name);
    setSearch("");
    setSearchDraft("");
    setPage(1);
  }

  return (
    <>
      <section className="library-heading">
        <div>
          <p className="eyebrow">视频资料库</p>
          <h1>我的视频资料库</h1>
          <p>添加视频后，系统会自动整理视频信息、章节和字幕。</p>
        </div>
        <div className="library-heading__actions">
          {capabilities.data?.chat === true ? (
            <RouteLink className="button button--quiet" to="/chat">AI 智能检索</RouteLink>
          ) : null}
          <button
            className="button button--primary"
            disabled={saveDisabled}
            aria-describedby={saveNoticeId}
            onClick={() => setDialogOpen(true)}
          >
            {saveButtonLabel}
          </button>
        </div>
      </section>

      {capabilities.isError ? (
        <aside className="library-notice library-notice--error" id="library-save-notice" role="alert">
          <div>
            <strong>暂时无法确认视频添加功能</strong>
            <p>资料库仍可浏览和检索。请检查网络后重新确认，不需要刷新整个页面。</p>
          </div>
          <button className="text-button" type="button" onClick={() => void capabilities.refetch()}>重新检查</button>
        </aside>
      ) : null}
      {capabilities.data?.save_enabled === false ? (
        <aside className="library-notice" id="library-save-notice" role="note">
          <div>
            <strong>当前部署暂未开放服务器端添加</strong>
            <p>已有视频仍可阅读和检索；若已安装浏览器插件，可从打开的视频页面保存字幕。</p>
          </div>
          <RouteLink className="text-button" to="/account/browser-companion">查看浏览器插件</RouteLink>
        </aside>
      ) : null}

      <section className="library-toolbar" aria-label="资料库筛选">
        <form
          ref={searchFormRef}
          className="search-box"
          role="search"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setSuggestionsOpen(false);
          }}
          onSubmit={(event) => { event.preventDefault(); submitSearch(searchDraft); }}
        >
          <label className="sr-only" htmlFor="library-search">搜索标题、作者或保存说明</label>
          <input
            id="library-search"
            name="search"
            type="search"
            autoComplete="off"
            value={searchDraft}
            aria-autocomplete="list"
            aria-controls={searchSuggestions.length > 0 ? "library-search-suggestions" : undefined}
            aria-expanded={suggestionsOpen && searchSuggestions.length > 0}
            onChange={(event) => { setSearchDraft(event.target.value); setSuggestionsOpen(true); }}
            onClick={() => setSuggestionsOpen(true)}
            onFocus={() => setSuggestionsOpen(true)}
            onKeyDown={(event) => { if (event.key === "Escape") setSuggestionsOpen(false); }}
            placeholder="搜索标题、作者或保存说明"
          />
          {suggestionsOpen && searchSuggestions.length > 0 ? (
            <ul id="library-search-suggestions" className="search-suggestions" aria-label="搜索建议">
              {searchSuggestions.map((suggestion) => (
                <li key={`${suggestion.kind}:${suggestion.value.toLocaleLowerCase()}`}>
                  <button
                    type="button"
                    title={suggestion.value}
                    aria-label={`${suggestion.kind} ${suggestion.value}`}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => submitSearch(suggestion.value)}
                  >
                    <span>{suggestion.kind}</span>
                    <strong>{suggestion.value}</strong>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <button type="submit">搜索</button>
        </form>
        <label className="select-field">
          <span className="sr-only">按处理状态筛选</span>
          <select name="lifecycle" value={lifecycle} onChange={(event) => changeFilter(event.target.value)}>
            <option value="">全部状态</option>
            <option value="ready">可阅读</option>
            <option value="queued">等待整理</option>
            <option value="processing">正在整理</option>
            <option value="needs_action">字幕不可用</option>
            <option value="failed">整理失败</option>
            <option value="archived">已归档</option>
          </select>
        </label>
        <label className="select-field">
          <span className="sr-only">排序方式</span>
          <select name="sort" value={sort} onChange={(event) => { setSort(event.target.value as LibraryQuery["sort"]); setPage(1); }}>
            <option value="saved_desc">最近添加</option>
            <option value="saved_asc">最早添加</option>
            <option value="title_asc">按标题</option>
          </select>
        </label>
      </section>

      {collectionOptions.length > 0 ? (
        <nav className="collection-filter" aria-label="收藏夹筛选">
          <span className="collection-filter__label">收藏夹</span>
          <div className="collection-options">
            <button className="collection-chip" type="button" aria-pressed={selectedCollection === null} onClick={() => selectCollection(null)}>全部视频</button>
            {collectionOptions.map((name) => (
              <button
                className="collection-chip"
                type="button"
                aria-pressed={selectedCollection?.toLocaleLowerCase("en") === name.toLocaleLowerCase("en")}
                key={name}
                onClick={() => selectCollection(name)}
              >
                {name}
              </button>
            ))}
          </div>
        </nav>
      ) : null}

      {library.isPending ? <LibraryLoadingState /> : null}
      {library.isError ? <LibraryErrorState description={libraryErrorDescription(library.error)} onRetry={() => void library.refetch()} /> : null}
      {library.isSuccess && library.data.items.length === 0 ? (
        <LibraryEmptyState
          trueFirstEmpty={library.data.is_true_first_empty && !query.search && !lifecycle}
          onAdd={saveDisabled ? undefined : () => setDialogOpen(true)}
        />
      ) : null}
      {library.isSuccess && library.data.items.length > 0 ? (
        <>
          <div className="library-summary"><span aria-label="当前可阅读视频数量">{readableItems.length} 个视频</span>{shouldPollLibrary(library.data.items) ? <span className="live-note" aria-live="polite"><i />正在自动更新状态</span> : null}</div>
          {readableItems.length > 0 ? (
            <section className="library-ready-zone" aria-label="可阅读视频">
              <div className={`video-grid${readableItems.length === 3 ? " video-grid--trio" : ""}`}>
                {readableItems.map((item) => <VideoCard item={item} key={item.public_id} />)}
              </div>
            </section>
          ) : null}
          {workItems.length > 0 ? (
            <section className="library-work-zone" aria-labelledby="library-work-zone-title">
              <header className="library-work-zone__header">
                <div>
                  <p className="eyebrow">整理状态</p>
                  <h2 id="library-work-zone-title">整理队列</h2>
                </div>
                <span aria-label="整理队列视频数量">{workItems.length} 个视频</span>
              </header>
              <div className="video-grid">
                {workItems.map((item) => <VideoCard item={item} key={item.public_id} />)}
              </div>
              {activeWorkItems.length > 0 ? <div className="work-progress">
                <div className="work-progress__label">
                  <span>当前整理进度</span>
                  <strong>约 {workProgress}%</strong>
                </div>
                <progress
                  aria-label="当前整理进度"
                  aria-valuetext={`约 ${workProgress}%`}
                  max="100"
                  value={workProgress}
                />
                <small>仅根据正在处理项目的当前阶段估算</small>
              </div> : null}
            </section>
          ) : null}
          {totalPages > 1 ? (
            <nav className="pagination" aria-label="资料库分页">
              <button className="button button--ghost" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
              <span>第 {page} / {totalPages} 页</span>
              <button className="button button--ghost" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button>
            </nav>
          ) : null}
        </>
      ) : null}

      <AddVideosDialog
        open={dialogOpen && !saveDisabled}
        onClose={() => setDialogOpen(false)}
        submitBatch={submitBatch}
        suggestedCollections={collectionOptions}
        onSubmitted={() => void queryClient.invalidateQueries({ queryKey: ["library"] })}
      />
    </>
  );
}
