"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, GripVertical, X } from "lucide-react";
import type { ChatResponse } from "@/lib/api/client";
import { SourceBrowser } from "@/components/source-browser";

type Citation = ChatResponse["citations"][number];

export function SourceSidePanel({ repositoryId, citations, index, onIndexChange, onClose }: {
  repositoryId: string;
  citations: Citation[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
}) {
  const [width, setWidth] = useState(800);
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const citation = citations[index];
  const resize = useCallback((clientX: number) => setWidth(Math.max(450, Math.min(window.innerWidth - 80, window.innerWidth - clientX))), []);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), a[href], summary, [tabindex="0"]')];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && (document.activeElement === first || !panelRef.current.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panelRef.current.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previousFocus?.focus(); };
  }, [onClose]);

  if (!citation) return null;
  return <div className="source-panel-layer"><button className="source-panel-backdrop" aria-label="Close source panel" onClick={onClose} /><aside ref={panelRef} role="dialog" aria-modal="true" aria-label={`Source citation ${citation.evidence_id}: ${citation.file_path}`} className="source-side-panel" style={{ width: `min(100vw, ${width}px)` }}>
    <div className="source-resize" role="separator" aria-label="Resize source panel" aria-orientation="vertical" tabIndex={0} onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) resize(event.clientX); }} onKeyDown={event => { if (event.key === "ArrowLeft") setWidth(value => Math.min(window.innerWidth - 80, value + 40)); if (event.key === "ArrowRight") setWidth(value => Math.max(450, value - 40)); }}><GripVertical size={15} /></div>
    <header className="source-panel-head"><div><span className="eyebrow">EVIDENCE / {citation.evidence_id} · {index + 1} OF {citations.length}</span><h2>{citation.file_path}</h2><p>Lines {citation.start_line}–{citation.end_line}{citation.qualified_symbol ? ` · ${citation.qualified_symbol}` : ""}</p></div><div className="source-panel-actions"><button type="button" aria-label="Previous citation" disabled={index === 0} onClick={() => onIndexChange(index - 1)}><ChevronLeft size={18} /></button><button type="button" aria-label="Next citation" disabled={index === citations.length - 1} onClick={() => onIndexChange(index + 1)}><ChevronRight size={18} /></button><button ref={closeRef} type="button" aria-label="Close source citation" onClick={onClose}><X size={18} /></button></div></header>
    <SourceBrowser key={`${repositoryId}:${index}:${citation.file_path}`} repositoryId={repositoryId} initialPath={citation.file_path} highlight={{ startLine: citation.start_line, endLine: citation.end_line }} />
    <p className="source-panel-note">Source is read from the current approved checkout. Reindex if it changed since this answer.</p>
  </aside></div>;
}
