import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";

import {
  ApiError,
  archiveItem,
  getLibraryItem,
  getTranscript,
  restoreItem,
  retryItem,
  updateWhySaved,
} from "../api/client";
import { LibraryErrorState, LibraryLoadingState } from "../library/LibraryStates";
import { RouteLink } from "../app/RouteTransition";
import { VideoDetailView } from "./VideoDetailView";

export function VideoDetailPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const item = useQuery({
    queryKey: ["library-item", id],
    queryFn: () => getLibraryItem(id),
    enabled: Boolean(id),
    refetchInterval: (state) => {
      const lifecycle = state.state.data?.lifecycle;
      return lifecycle === "queued" || lifecycle === "processing" ? 4_000 : false;
    },
  });
  const transcript = useInfiniteQuery({
    queryKey: ["transcript", id],
    queryFn: ({ pageParam }) => getTranscript(id, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: item.data?.lifecycle === "ready",
    // The transcript is read from object storage after the library item has
    // already reached `ready`. A short object-store/network hiccup should not
    // strand the detail page in the error state; deterministic contract
    // failures (invalid data, a stale cursor, or an unavailable transcript)
    // still stop after this small bounded retry budget.
    retry: (failureCount, error) =>
      error instanceof ApiError &&
      error.code === "transcript_unavailable" &&
      failureCount < 2,
    retryDelay: (attemptIndex) => Math.min(1_000 * 2 ** attemptIndex, 3_000),
  });
  const action = useMutation({
    mutationFn: async (input: { type: "archive" | "restore" | "retry" | "why"; value?: string | null }) => {
      if (input.type === "archive") return archiveItem(id);
      if (input.type === "restore") return restoreItem(id);
      if (input.type === "retry") return retryItem(id);
      return updateWhySaved(id, input.value ?? null);
    },
    onSuccess: (nextItem) => {
      queryClient.setQueryData(["library-item", id], nextItem);
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  return (
    <>
      <RouteLink className="back-link" to="/library">← 返回资料库</RouteLink>
      {item.isPending ? <LibraryLoadingState label="正在加载视频详情" /> : null}
      {item.isError ? (
        <LibraryErrorState
          onRetry={() => void item.refetch()}
          title="暂时无法加载视频详情"
          description="请检查网络后重新加载。"
        />
      ) : null}
      {item.data ? (
        <VideoDetailView
          item={item.data}
          transcriptPages={transcript.data?.pages ?? []}
          transcriptPending={transcript.isFetchingNextPage}
          transcriptInitialPending={transcript.isPending && item.data.lifecycle === "ready"}
          transcriptError={transcript.isError}
          actionPending={action.isPending}
          actionError={action.isError}
          onLoadMore={() => void transcript.fetchNextPage()}
          onRetryTranscript={() => void transcript.refetch()}
          onArchive={() => action.mutate({ type: "archive" })}
          onRestore={() => action.mutate({ type: "restore" })}
          onRetry={() => action.mutate({ type: "retry" })}
          onUpdateWhySaved={async (value) => {
            await action.mutateAsync({ type: "why", value });
          }}
        />
      ) : null}
    </>
  );
}
