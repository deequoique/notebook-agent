import { BrandLogo } from "../app/BrandLogo";

interface EmptyProps {
  trueFirstEmpty: boolean;
  onAdd?: () => void;
}

export function LibraryEmptyState({ trueFirstEmpty, onAdd }: EmptyProps) {
  if (!trueFirstEmpty) {
    return (
      <section className="state-card state-card--compact">
        <p className="eyebrow">筛选结果</p>
        <h2>没有符合当前条件的视频</h2>
        <p>请更换关键词或筛选条件。</p>
      </section>
    );
  }
  return (
    <section className="state-card welcome-card">
      <BrandLogo className="agent-mark" />
      <p className="eyebrow">开始使用</p>
      <h2>资料库还是空的</h2>
      <p>可以添加 YouTube 链接；NTULearn 和浏览器内的 YouTube 字幕也可通过浏览器伴侣保存。</p>
      {onAdd ? <button className="button button--primary" onClick={onAdd}>添加第一个视频</button> : null}
    </section>
  );
}

export function LibraryLoadingState({ label = "正在加载资料库" }: { label?: string } = {}) {
  return (
    <div className="skeleton-list" aria-label={label} aria-busy="true">
      {[0, 1, 2].map((index) => <div className="video-skeleton" key={index} />)}
    </div>
  );
}

export function LibraryErrorState({
  onRetry,
  title = "暂时无法打开资料库",
  description = "请检查网络后重新加载。",
}: {
  onRetry: () => void;
  title?: string;
  description?: string;
}) {
  return (
    <section className="state-card state-card--compact" role="alert">
      <p className="eyebrow">加载失败</p>
      <h2>{title}</h2>
      <p>{description}</p>
      <button className="button button--quiet" onClick={onRetry}>重新加载</button>
    </section>
  );
}
