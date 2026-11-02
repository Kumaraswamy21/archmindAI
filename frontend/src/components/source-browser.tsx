"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, FileCode2, Folder, LoaderCircle, Search } from "lucide-react";
import { api, ApiError, type RepositoryFiles, type SourceContent } from "@/lib/api/client";
import { tokenizeJava } from "@/lib/java-tokens";

export type SourceRange = { startLine: number; endLine: number };
type TreeNode = { name: string; path: string; children: Map<string, TreeNode>; file: boolean };

function buildTree(paths: string[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map(), file: false };
  for (const path of paths) {
    let node = root;
    for (const [index, name] of path.split("/").entries()) {
      const nextPath = node.path ? `${node.path}/${name}` : name;
      if (!node.children.has(name)) node.children.set(name, { name, path: nextPath, children: new Map(), file: index === path.split("/").length - 1 });
      node = node.children.get(name)!;
    }
  }
  return root;
}

function TreeBranch({ node, selected, onSelect }: { node: TreeNode; selected: string | null; onSelect: (path: string) => void }) {
  const children = [...node.children.values()].sort((a, b) => Number(a.file) - Number(b.file) || a.name.localeCompare(b.name));
  return <ul className="source-tree-list">{children.map(child => <li key={child.path}>{child.file
    ? <button type="button" className={`source-tree-file${selected === child.path ? " selected" : ""}`} aria-label={`Open ${child.path}`} aria-current={selected === child.path ? "page" : undefined} onClick={() => onSelect(child.path)}><FileCode2 size={14} />{child.name}</button>
    : <details open key={child.path}><summary><ChevronRight size={13} /><Folder size={14} />{child.name}</summary><TreeBranch node={child} selected={selected} onSelect={onSelect} /></details>}</li>)}</ul>;
}

export function SourceViewer({ repositoryId, path, highlight }: { repositoryId: string; path: string | null; highlight?: SourceRange }) {
  const [source, setSource] = useState<SourceContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const rangeStart = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api.sourceFile(repositoryId, path, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (result.repository_id !== repositoryId || result.file_path !== path) {
        setError("The source response did not match the selected repository and file.");
        return;
      }
      setSource(result);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof ApiError ? cause.message : "This source file could not be loaded.");
    });
    return () => controller.abort();
  }, [repositoryId, path, retry]);

  useEffect(() => {
    if (source && highlight && rangeStart.current?.scrollIntoView) rangeStart.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [source, highlight]);

  const lines = useMemo(() => source ? source.content.replace(/\n$/, "").split("\n") : [], [source]);
  const javaLines = useMemo(() => source && (source.language?.toLowerCase() === "java" || source.file_path.endsWith(".java")) ? tokenizeJava(source.content) : null, [source]);
  const stale = Boolean(source && highlight && (highlight.startLine < 1 || highlight.endLine < highlight.startLine || highlight.endLine > lines.length));

  if (!path) return <div className="source-empty"><FileCode2 size={25} /><strong>Select a file</strong><span>Choose a file from the repository tree to read its source.</span></div>;
  return <section className="source-viewer" aria-label={`Source viewer for ${path}`}>
    <div className="source-viewer-head"><div><span className="eyebrow">READ ONLY SOURCE</span><h3>{path}</h3></div>{source && <small>{lines.length} lines</small>}</div>
    {error ? <div className="source-state" role="alert"><strong>Source unavailable</strong><span>{error} The file may have moved since indexing.</span><button type="button" onClick={() => { setSource(null); setError(null); setRetry(value => value + 1); }}>Retry loading</button></div>
      : !source ? <div className="source-state" role="status"><LoaderCircle size={17} className="spin" /> Loading source...</div>
        : <>{stale && <div className="source-stale" role="alert">The cited line range is no longer present in this file. Reindex the repository and ask the question again.</div>}
          <div className="source-code" aria-label="Source code">{lines.map((line, index) => {
            const number = index + 1;
            const cited = Boolean(highlight && !stale && number >= highlight.startLine && number <= highlight.endLine);
            return <div key={number} ref={cited && number === highlight?.startLine ? rangeStart : undefined} className={`source-code-line${cited ? " source-code-cited" : ""}`} data-line={number} data-highlighted={cited ? "true" : undefined}><span aria-hidden="true">{number}</span><code>{javaLines ? javaLines[index]?.map((token, tokenIndex) => <span key={tokenIndex} className={`java-${token.kind}`}>{token.text}</span>) : line}{!line && " "}</code></div>;
          })}</div></>}
  </section>;
}

export function SourceBrowser({ repositoryId, initialPath, highlight }: { repositoryId: string; initialPath?: string; highlight?: SourceRange }) {
  const [listing, setListing] = useState<RepositoryFiles | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(initialPath ?? null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.repositoryFiles(repositoryId, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (result.repository_id !== repositoryId) { setError("The file list did not match the selected repository."); return; }
      setListing(result);
    })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof ApiError ? cause.message : "Repository files could not be loaded."); });
    return () => controller.abort();
  }, [repositoryId, retry]);

  const paths = useMemo(() => listing?.files.filter(file => file.path.toLowerCase().includes(query.trim().toLowerCase())).map(file => file.path) ?? [], [listing, query]);
  const tree = useMemo(() => buildTree(paths), [paths]);
  const selectionIsListed = !selected || !listing || listing.files.some(file => file.path === selected);
  return <div className="source-browser">
    <aside className="source-files" aria-label="Repository files"><div className="source-files-head"><strong>Files</strong>{listing && <small>{listing.total_files}</small>}</div><label className="source-search"><Search size={14} /><input aria-label="Search repository files" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search files..." /></label>
      {error ? <div className="source-files-state" role="alert">{error}<button type="button" onClick={() => { setError(null); setRetry(value => value + 1); }}>Retry</button></div> : !listing ? <div className="source-files-state" role="status">Loading files...</div> : paths.length ? <nav aria-label="File tree"><TreeBranch node={tree} selected={selected} onSelect={setSelected} /></nav> : <div className="source-files-state">{query ? "No matching files." : "No files found in this repository."}</div>}
    </aside>
    {!selectionIsListed ? <div className="source-state source-missing" role="alert"><strong>Referenced file is unavailable</strong><span>This citation points to a file that is no longer in the repository listing. Reindex and ask again.</span></div> : <SourceViewer key={`${repositoryId}:${selected ?? ""}`} repositoryId={repositoryId} path={selected} highlight={selected === initialPath ? highlight : undefined} />}
  </div>;
}
