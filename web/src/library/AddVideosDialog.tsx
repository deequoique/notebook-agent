import { useEffect, useRef, useState } from "react";

import { submitVideoBatch } from "../api/client";
import type { BatchSubmitInput, BatchSubmitResponse } from "../api/contracts";
import {
  collectCollectionNames,
  formatWhySaved,
  parseWhySaved,
  validateCollectionName,
  WHY_SAVED_MAX_LENGTH,
} from "./collections";

interface AddVideosDialogProps {
  open: boolean;
  onClose: () => void;
  suggestedCollections?: readonly string[];
  submitBatch?: (input: BatchSubmitInput) => Promise<BatchSubmitResponse>;
  onSubmitted?: (result: BatchSubmitResponse) => void;
}

const resultCopy: Record<string, string> = {
  queued: "已添加，等待整理",
  already_exists: "资料库中已有此视频",
  unsupported_url: "暂不支持这个链接",
  invalid_url: "链接格式不正确",
  queue_unavailable: "暂时无法开始整理，请稍后重试",
  create_failed: "添加失败，请稍后重试",
  quota_exceeded: "已达到保存上限",
};

export function AddVideosDialog({
  open,
  onClose,
  suggestedCollections = [],
  submitBatch = submitVideoBatch,
  onSubmitted,
}: AddVideosDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [urlDraft, setUrlDraft] = useState("");
  const [whySaved, setWhySaved] = useState("");
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [newCollection, setNewCollection] = useState("");
  const [createdCollections, setCreatedCollections] = useState<string[]>([]);
  const [collectionError, setCollectionError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BatchSubmitResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionGenerationRef = useRef(0);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (dialog.open) dialog.close();
    submissionGenerationRef.current += 1;
    setUrls([]);
    setUrlDraft("");
    setWhySaved("");
    setSelectedCollection(null);
    setNewCollection("");
    setCreatedCollections([]);
    setCollectionError(null);
    setError(null);
    setResult(null);
    setSubmitting(false);
  }, [open]);

  const collectionOptions = collectCollectionNames([
    ...suggestedCollections.map((name) => `#${name}`),
    ...createdCollections.map((name) => `#${name}`),
  ]);

  function selectCollection(name: string | null) {
    setSelectedCollection(name);
    setCollectionError(null);
  }

  function createCollection() {
    const validationError = validateCollectionName(newCollection);
    if (validationError) {
      setCollectionError(validationError);
      return;
    }
    const formatted = formatWhySaved("", newCollection);
    const name = parseWhySaved(formatted.value).collections[0];
    if (!name) return;
    setCreatedCollections((current) => collectCollectionNames([
      ...current.map((value) => `#${value}`),
      `#${name}`,
    ]));
    setSelectedCollection(name);
    setNewCollection("");
    setCollectionError(null);
  }

  function addUrlTags(values: readonly string[]) {
    const normalized = values.map((value) => value.trim()).filter(Boolean);
    if (normalized.length === 0) return;
    setUrls((current) => [...current, ...normalized]);
    setError(null);
  }

  function commitUrlDraft() {
    if (!urlDraft.trim()) return;
    addUrlTags([urlDraft]);
    setUrlDraft("");
  }

  function removeUrl(index: number) {
    setUrls((current) => current.filter((_, currentIndex) => currentIndex !== index));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const submittedUrls = [...urls, urlDraft.trim()].filter(Boolean);
    if (submittedUrls.length === 0) {
      setError("请至少粘贴一个 YouTube 或 Bilibili 链接。");
      return;
    }
    if (submittedUrls.length > 10) {
      setError("一次最多添加 10 个链接。");
      return;
    }
    const formattedWhySaved = formatWhySaved(whySaved, selectedCollection);
    if (formattedWhySaved.error) {
      setError(formattedWhySaved.error);
      return;
    }
    setError(null);
    setUrls(submittedUrls);
    setUrlDraft("");
    setSubmitting(true);
    const submissionGeneration = ++submissionGenerationRef.current;
    try {
      const nextResult = await submitBatch({ urls: submittedUrls, why_saved: formattedWhySaved.value });
      if (submissionGeneration !== submissionGenerationRef.current) return;
      setResult(nextResult);
      onSubmitted?.(nextResult);
    } catch {
      if (submissionGeneration !== submissionGenerationRef.current) return;
      setError("添加未完成，请检查网络后重试。");
    } finally {
      if (submissionGeneration === submissionGenerationRef.current) setSubmitting(false);
    }
  }

  return (
    <dialog
      className="add-dialog"
      ref={dialogRef}
      aria-labelledby="add-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <form className="add-form" onSubmit={handleSubmit}>
        <header className="dialog-header">
          <div>
            <p className="eyebrow">一次最多添加 10 个</p>
            <h2 id="add-dialog-title">添加视频链接</h2>
          </div>
          <button className="icon-button" type="button" aria-label="关闭添加视频窗口" onClick={onClose}>×</button>
        </header>
        <div className="field url-field">
          <span className="field-label-row">
            <label htmlFor="url-draft">YouTube 或 Bilibili 链接，每行一个</label>
            <small className="url-count" data-over-limit={urls.length > 10}>{urls.length} / 10</small>
          </span>
          <p className="field-help" id="url-draft-help">粘贴链接后按 Enter；也可以一次粘贴多行。</p>
          <p className="field-help">支持 YouTube 与 Bilibili 普通视频链接。NTULearn 或服务器暂时无法读取的 YouTube 视频，可通过 <a href="/account/browser-companion">浏览器伴侣</a>保存。</p>
          <div className="url-token-input">
            {urls.length > 0 ? (
              <ol className="url-tag-list" aria-label="已添加的视频链接">
                {urls.map((url, index) => (
                  <li className="url-tag" key={`${url}-${index}`}>
                    <span title={url}>{url}</span>
                    <button type="button" aria-label={`移除链接 ${index + 1}`} onClick={() => removeUrl(index)}>×</button>
                  </li>
                ))}
              </ol>
            ) : null}
            <textarea
              id="url-draft"
              className="url-draft-input"
              name="urls"
              aria-describedby="url-draft-help"
              autoComplete="off"
              spellCheck={false}
              rows={1}
              value={urlDraft}
              placeholder={urls.length === 0 ? "粘贴 YouTube 或 Bilibili 链接" : "继续添加链接"}
              onChange={(event) => {
                const next = event.target.value;
                if (/\r?\n/u.test(next)) {
                  addUrlTags(next.split(/\r?\n/u));
                  setUrlDraft("");
                  return;
                }
                setUrlDraft(next);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                commitUrlDraft();
              }}
            />
          </div>
        </div>
        <fieldset className="collection-picker">
          <legend>保存到收藏夹（可选）</legend>
          <p className="field-help">选择已有收藏夹，或新建一个标签。未选择时保存为未归类。</p>
          <div className="collection-options" aria-label="选择收藏夹">
            <button
              className="collection-chip"
              type="button"
              aria-pressed={selectedCollection === null}
              onClick={() => selectCollection(null)}
            >
              未归类
            </button>
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
          <div className="collection-create">
            <label className="field">
              <span>新收藏夹名称</span>
              <input
                name="new-collection"
                autoComplete="off"
                value={newCollection}
                maxLength={21}
                placeholder="例如：产品调研"
                onChange={(event) => setNewCollection(event.target.value)}
              />
            </label>
            <button className="button button--quiet" type="button" onClick={createCollection}>
              创建并选择
            </button>
          </div>
          {collectionError ? <p className="inline-error" role="alert">{collectionError}</p> : null}
        </fieldset>
        <div className="field">
          <span className="field-label-row">
            <label htmlFor="why-saved">备注（可选）</label>
            <small>{whySaved.length} / {WHY_SAVED_MAX_LENGTH}</small>
          </span>
          <textarea
            id="why-saved"
            className="why-saved-textarea"
            name="why-saved"
            autoComplete="off"
            value={whySaved}
            rows={3}
            maxLength={WHY_SAVED_MAX_LENGTH}
            placeholder="例如：用于周末精读或项目调研"
            onChange={(event) => setWhySaved(event.target.value)}
          />
        </div>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        {result ? (
          <ol className="submission-results" aria-label="添加结果" aria-live="polite">
            {result.results.map((item) => (
              <li key={`${item.input_index}-${item.status}`} data-status={item.status}>
                <span>第 {item.input_index + 1} 个链接</span>
                <strong>{resultCopy[item.status] ?? "未能确认处理结果，请稍后查看资料库"}</strong>
              </li>
            ))}
          </ol>
        ) : null}
        <button className="button button--primary button--wide" disabled={submitting} type="submit">
          {submitting ? "正在添加…" : "添加并整理"}
        </button>
      </form>
    </dialog>
  );
}
