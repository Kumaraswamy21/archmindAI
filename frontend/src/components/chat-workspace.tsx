"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity, ArrowUpRight, Bot, CheckCircle2, CircleAlert, FileCode2,
  LoaderCircle, LockKeyhole, RotateCcw, Send, Sparkles, Square, UserRound,
} from "lucide-react";
import { api, ApiError, type ChatResponse } from "@/lib/api/client";
import { submitChat, type ChatStreamEvent, type ChatTransport } from "@/lib/chat-stream";
import { SourceSidePanel } from "@/components/source-side-panel";

type Turn = {
  id: number;
  question: string;
  status: "pending" | "completed" | "failed" | "cancelled";
  answer: ChatResponse | null;
  error: string | null;
  events: ChatStreamEvent[];
  transport: ChatTransport | null;
};
type Citation = ChatResponse["citations"][number];

const suggestions = [
  "Where is authentication implemented?",
  "Which components depend on the service layer?",
  "How does a request move through this repository?",
];

function formatAgent(name: unknown) {
  if (typeof name !== "string") return "Agent";
  return name.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function activityText(event: ChatStreamEvent): [string, string] {
  const payload = event.payload;
  switch (event.event_type) {
    case "workflow_started": return ["Workflow started", "The backend accepted this repository-scoped question."];
    case "intent_classified": return ["Intent classified", `${String(payload.intent ?? "Unknown")} · ${formatAgent(payload.selected_workflow)}`];
    case "agent_started": return [`${formatAgent(payload.agent)} started`, "Specialist analysis is running."];
    case "agent_completed": return [`${formatAgent(payload.agent)} completed`, `${String(payload.evidence_count ?? "—")} evidence items`];
    case "evidence_verified": return ["Evidence verified", String(payload.verification_status ?? "Verification complete")];
    case "answer_completed": return ["Answer completed", "Verified response received."];
    case "workflow_failed": return ["Workflow failed", String(payload.code ?? "WORKFLOW_FAILED")];
  }
}

function MarkdownAnswer({ text }: { text: string }) {
  return <div className="chat-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => href && /^https?:\/\//i.test(href)
      ? <a href={href} target="_blank" rel="noopener noreferrer">{children}<ArrowUpRight size={13} /></a>
      : <span>{children}</span>,
  }}>{text}</ReactMarkdown></div>;
}

export function ChatWorkspace({ repositoryId }: { repositoryId: string }) {
  const [ready, setReady] = useState(false);
  const [checking, setChecking] = useState(true);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selectedTurnId, setSelectedTurnId] = useState<number | null>(null);
  const [citationSelection, setCitationSelection] = useState<{ citations: Citation[]; index: number } | null>(null);
  const [gateError, setGateError] = useState<string | null>(null);
  const sequence = useRef(0);
  const busy = useRef(false);
  const current = useRef<AbortController | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.indexStatus(repositoryId, controller.signal).then(status => {
      if (!controller.signal.aborted) {
        setReady(status.index_status === "READY");
        setChecking(false);
      }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setGateError(cause instanceof ApiError ? cause.message : "Index coverage is unavailable.");
        setReady(false);
        setChecking(false);
      }
    });
    return () => { controller.abort(); current.current?.abort(); };
  }, [repositoryId]);

  const updateTurn = useCallback((id: number, change: (turn: Turn) => Turn) => {
    setTurns(existing => existing.map(turn => turn.id === id ? change(turn) : turn));
  }, []);
  const closeCitation = useCallback(() => setCitationSelection(null), []);

  async function run(text: string, retryId?: number) {
    const trimmed = text.trim();
    if (!ready || !trimmed || trimmed.length > 2000 || busy.current) return;
    busy.current = true;
    const id = retryId ?? ++sequence.current;
    const controller = new AbortController();
    current.current = controller;
    setSelectedTurnId(id);
    if (retryId === undefined) {
      setTurns(existing => [...existing, {
        id, question: trimmed, status: "pending", answer: null,
        error: null, events: [], transport: null,
      }]);
      setQuestion("");
    } else {
      updateTurn(id, turn => ({ ...turn, status: "pending", answer: null, error: null, events: [], transport: null }));
    }
    try {
      const coverage = await api.indexStatus(repositoryId, controller.signal);
      if (coverage.index_status !== "READY") {
        setReady(false);
        throw new ApiError("INDEX_NOT_READY", "The repository index is no longer ready. Return to the project overview to check its status.");
      }
      const { answer, transport } = await submitChat(
        { repository_id: repositoryId, message: trimmed, conversation_id: null },
        controller.signal,
        event => updateTurn(id, turn => ({ ...turn, events: [...turn.events, event] })),
      );
      if (!controller.signal.aborted) {
        updateTurn(id, turn => ({ ...turn, status: "completed", answer, transport }));
      }
    } catch (cause) {
      if (controller.signal.aborted || (cause instanceof Error && cause.name === "AbortError")) {
        updateTurn(id, turn => ({ ...turn, status: "cancelled", error: "Analysis cancelled." }));
      } else {
        updateTurn(id, turn => ({
          ...turn, status: "failed",
          error: cause instanceof ApiError ? cause.message : "Analysis failed. Please try again.",
        }));
      }
    } finally {
      busy.current = false;
      current.current = null;
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void run(question); }
  function cancel() { current.current?.abort(); }
  const selected = turns.find(turn => turn.id === selectedTurnId) ?? turns.at(-1) ?? null;
  const pending = turns.some(turn => turn.status === "pending");

  return <div className="chat-workspace">
    <div className="chat-heading"><div><div className="eyebrow with-rule">ARCHMIND / AI WORKSPACE</div><h2>Ask the codebase.</h2><p>Repository-scoped analysis with traceable evidence and agent activity.</p></div><span className="chat-context-pill"><Sparkles size={14} /> CONTEXT: THIS REPOSITORY</span></div>
    {!checking && !ready && <div className="ai-gate" role="status"><LockKeyhole size={19} /><div><strong>Index required</strong><span>{gateError ?? "Complete indexing and wait for the backend to report a ready index before asking questions."}</span></div></div>}
    <div className="chat-layout">
      <section className="chat-main" aria-label="Chat messages">
        <div className="chat-messages" aria-live="polite">
          {turns.length === 0 && <div className="chat-empty"><div className="chat-empty-icon"><Sparkles size={27} /></div><h3>Begin with a question.</h3><p>Explore implementation details, dependencies, and architecture. Each answer is checked against repository evidence.</p><div className="suggestions" aria-label="Suggested questions">{suggestions.map(item => <button key={item} type="button" onClick={() => { setQuestion(item); composer.current?.focus(); }} disabled={!ready || pending}>{item}<ArrowUpRight size={14} /></button>)}</div></div>}
          {turns.map(turn => <div className="chat-turn" key={turn.id}>
            <div className="chat-message chat-user"><div className="chat-avatar"><UserRound size={16} /></div><div><span className="chat-speaker">YOU</span><p>{turn.question}</p></div></div>
            <div className="chat-message chat-assistant"><div className="chat-avatar"><Bot size={17} /></div><div className="chat-answer-body"><span className="chat-speaker">ARCHMIND</span>
              {turn.status === "pending" ? <div className="chat-pending" role="status"><LoaderCircle size={16} className="spin" /> Following the workflow...</div>
                : turn.status === "completed" && turn.answer ? <>
                  <MarkdownAnswer text={turn.answer.answer} />
                  <div className="chat-answer-footer"><span className="verification-badge"><CheckCircle2 size={14} /> {turn.answer.verification_status.replaceAll("_", " ")}</span><button type="button" onClick={() => setSelectedTurnId(turn.id)}>View agent activity</button></div>
                  {turn.answer.citations.length > 0 && <div className="chat-citations"><strong>SOURCE CITATIONS</strong>{turn.answer.citations.map((item, index) => <button type="button" key={`${item.evidence_id}:${index}`} onClick={() => setCitationSelection({ citations: turn.answer!.citations, index })}><FileCode2 size={15} /><span><b>{item.evidence_id}</b> {item.file_path}:{item.start_line}–{item.end_line}{item.qualified_symbol ? ` · ${item.qualified_symbol}` : ""}</span><ArrowUpRight size={14} /></button>)}</div>}
                </> : <div className="chat-error" role="alert"><CircleAlert size={16} /><div><span>{turn.error ?? "Analysis failed."}</span><button type="button" onClick={() => void run(turn.question, turn.id)} disabled={pending}><RotateCcw size={14} /> Retry question</button></div></div>}
            </div></div>
          </div>)}
        </div>
        <form className="chat-composer" onSubmit={submit}><label htmlFor="chat-question">Ask about this repository</label><textarea ref={composer} id="chat-question" value={question} onChange={event => setQuestion(event.target.value)} placeholder="Ask about a service, dependency, or design decision..." rows={3} maxLength={2000} disabled={!ready || pending} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (!pending) void run(question); } }} /><div><span>{checking ? "Checking index..." : ready ? "Answers use the active index · No conversation memory" : "Index must be ready to analyze"}</span>{pending ? <button className="button button-secondary" type="button" onClick={cancel}><Square size={15} /> Cancel</button> : <button className="button button-primary" type="submit" disabled={!ready || !question.trim()}><Send size={16} /> Send question</button>}</div></form>
      </section>
      <ActivityPanel turn={selected} />
    </div>
    {citationSelection && <SourceSidePanel repositoryId={repositoryId} citations={citationSelection.citations} index={citationSelection.index} onIndexChange={index => setCitationSelection(current => current ? { ...current, index } : null)} onClose={closeCitation} />}
  </div>;
}

function ActivityPanel({ turn }: { turn: Turn | null }) {
  return <aside className="activity-panel" aria-label="Agent activity"><div className="activity-head"><Activity size={18} /><div><span className="eyebrow">LIVE WORKFLOW</span><h3>Agent activity</h3></div></div>
    {turn ? <><div className="activity-question">{turn.question}</div>{turn.events.length > 0 ? <ol className="activity-list">{turn.events.map(event => { const [title, detail] = activityText(event); return <li key={`${event.request_id}-${event.sequence}`}><span className="activity-node" /><div><strong>{title}</strong><small>{detail}</small></div></li>; })}</ol> : <div className="activity-empty">{turn.status === "pending" ? "Waiting for live workflow events..." : turn.transport === "standard" ? "Live events were unavailable. The standard chat endpoint returned the final answer." : "No live events were received for this attempt."}</div>}
      {turn.answer && <div className="activity-summary"><span className="eyebrow">RUN SUMMARY</span><dl><div><dt>Intent</dt><dd>{turn.answer.classified_intent}</dd></div><div><dt>Agents</dt><dd>{turn.answer.agents_executed.map(formatAgent).join(", ") || "None"}</dd></div><div><dt>Verification</dt><dd>{turn.answer.verification_status}</dd></div><div><dt>Status</dt><dd>{turn.answer.workflow_status}</dd></div></dl></div>}</> : <div className="activity-empty">Live workflow events appear here when you ask a question. Only stages reported by the backend are shown.</div>}
  </aside>;
}
