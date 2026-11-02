"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ReactFlow, Background, BackgroundVariant, Controls, type ReactFlowInstance } from "@xyflow/react";
import { ArrowUpRight, CircleAlert, Focus, GitBranch, LoaderCircle, Search, X } from "lucide-react";
import { api, ApiError, type DependencyList, type DependencyRelationship } from "@/lib/api/client";
import { GRAPH_EDGE_LIMIT, mapDependencyGraph, type GraphEdge, type GraphNode } from "@/lib/dependency-graph";

type RelationshipType = DependencyRelationship["relationship_type"];
type ResolutionStatus = DependencyRelationship["resolution_status"];
const kinds: RelationshipType[] = ["IMPORTS", "EXTENDS", "IMPLEMENTS", "INJECTS", "CALLS"];

function sourceHref(repositoryId: string, relationship: DependencyRelationship) {
  const params = new URLSearchParams({ path: relationship.file_path, start_line: String(relationship.start_line), end_line: String(relationship.end_line) });
  return `/projects/${encodeURIComponent(repositoryId)}/explore?${params}`;
}

export function DependencyExplorer({ repositoryId }: { repositoryId: string }) {
  const [data, setData] = useState<DependencyList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState("");
  const [focusedSymbol, setFocusedSymbol] = useState<string | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [relationshipType, setRelationshipType] = useState<RelationshipType | "ALL">("ALL");
  const [resolution, setResolution] = useState<ResolutionStatus | "ALL">("ALL");
  const [flow, setFlow] = useState<ReactFlowInstance<GraphNode, GraphEdge> | null>(null);
  const [symbolKind, setSymbolKind] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.dependencies(repositoryId, { symbol: focusedSymbol ?? undefined, relationshipType: relationshipType === "ALL" ? undefined : relationshipType, limit: GRAPH_EDGE_LIMIT }, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        if (result.repository_id !== repositoryId || result.relationships.some(item => item.repository_id !== repositoryId)) {
          setError("Dependency data did not match the selected repository.");
          setData(null);
          return;
        }
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) { setError(cause instanceof ApiError ? cause.message : "Dependencies could not be loaded."); setData(null); }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [repositoryId, focusedSymbol, relationshipType, retry]);

  const filtered = useMemo(() => data?.relationships.filter(item => resolution === "ALL" || item.resolution_status === resolution) ?? [], [data, resolution]);
  const graph = useMemo(() => mapDependencyGraph(filtered, repositoryId), [filtered, repositoryId]);
  const symbols = useMemo(() => [...new Set(data?.relationships.flatMap(item => [item.source_symbol, item.target_symbol]) ?? [])].sort(), [data]);
  const matches = useMemo(() => query.trim() ? symbols.filter(symbol => symbol.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8) : [], [query, symbols]);
  const selectedEdges = useMemo(() => selectedSymbol ? filtered.filter(item => item.source_symbol === selectedSymbol || item.target_symbol === selectedSymbol) : [], [filtered, selectedSymbol]);

  useEffect(() => {
    if (selectedSymbol && flow && graph.nodes.some(node => node.id === selectedSymbol)) {
      void flow.fitView({ nodes: [{ id: selectedSymbol }], maxZoom: 1.15, duration: 250 });
    }
  }, [selectedSymbol, flow, graph.nodes]);

  useEffect(() => {
    if (!selectedSymbol || !selectedEdges.length || selectedSymbol.startsWith("file:")) return;
    const controller = new AbortController();
    api.javaSymbols(repositoryId, selectedEdges[0].file_path, controller.signal).then(result => {
      if (controller.signal.aborted || result.file_path !== selectedEdges[0].file_path) return;
      const match = result.symbols.find(symbol => symbol.qualified_name === selectedSymbol);
      setSymbolKind(match?.kind ?? "Java symbol");
    }).catch(() => { if (!controller.signal.aborted) setSymbolKind("Java symbol"); });
    return () => controller.abort();
  }, [repositoryId, selectedSymbol, selectedEdges]);

  function focus(symbol: string) {
    if (!symbol) return;
    const exact = symbols.find(item => item === symbol) ?? (matches.length === 1 ? matches[0] : symbol);
    setSelectedSymbol(exact);
    if (focusedSymbol === exact) return;
    setFocusedSymbol(exact);
    setSymbolKind(null);
    setLoading(true);
  }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); focus(query.trim()); }
  function clearFocus() { setFocusedSymbol(null); setSelectedSymbol(null); setQuery(""); setSymbolKind(null); setLoading(true); }

  return <div className="dependency-explorer">
    <div className="dependency-heading"><div><span className="eyebrow with-rule">ARCHMIND / STATIC DEPENDENCIES</span><h1>Dependency explorer.</h1><p>Trace relationships extracted from Java source in this repository.</p></div><span className="dependency-count"><GitBranch size={16} /> {data?.total_relationships ?? "—"} relationships</span></div>
    <div className="dependency-toolbar"><form onSubmit={submit} className="dependency-search"><Search size={15} /><input aria-label="Search symbols" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search symbols or enter an exact name" /><button type="submit" disabled={!query.trim()}>Focus</button></form><label>Relationship<select aria-label="Filter relationship type" value={relationshipType} onChange={event => { setRelationshipType(event.target.value as RelationshipType | "ALL"); setLoading(true); }}><option value="ALL">All types</option>{kinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label><label>Resolution<select aria-label="Filter resolution status" value={resolution} onChange={event => setResolution(event.target.value as ResolutionStatus | "ALL")}><option value="ALL">All statuses</option><option value="RESOLVED">Resolved</option><option value="UNRESOLVED">Unresolved</option></select></label>{focusedSymbol && <button type="button" className="dependency-clear" onClick={clearFocus}><X size={14} /> Clear focus</button>}</div>
    {matches.length > 0 && <div className="dependency-matches" aria-label="Matching symbols">{matches.map(symbol => <button type="button" key={symbol} onClick={() => { setQuery(symbol); focus(symbol); }}><Focus size={13} />{symbol}</button>)}</div>}
    {focusedSymbol && <div className="dependency-focus">Focused on <strong>{focusedSymbol}</strong> · showing its direct relationships</div>}
    {error ? <div className="dependency-state" role="alert"><CircleAlert size={22} /><h2>Dependencies unavailable</h2><p>{error}</p><button type="button" onClick={() => { setLoading(true); setRetry(value => value + 1); }}>Retry loading</button></div>
      : loading ? <div className="dependency-state" role="status"><LoaderCircle size={22} className="spin" /><p>Loading dependency relationships...</p></div>
        : !graph.edges.length ? <div className="dependency-state"><GitBranch size={23} /><h2>{data?.total_relationships === 0 && !focusedSymbol && relationshipType === "ALL" ? "No dependencies found" : "No relationships match this view"}</h2><p>{data?.total_relationships === 0 && !focusedSymbol && relationshipType === "ALL" ? "Index the repository to extract static Java relationships, then return here." : "Try another relationship type, resolution status, or symbol."}</p></div>
          : <div className="dependency-body"><div className="dependency-canvas" aria-label="Dependency graph"><ReactFlow<GraphNode, GraphEdge> nodes={graph.nodes.map(node => ({ ...node, selected: node.id === selectedSymbol }))} edges={graph.edges} onInit={setFlow} onNodeClick={(_event, node) => { setSelectedSymbol(node.id); setSymbolKind(null); }} onEdgeClick={(_event, edge) => { setSelectedSymbol(edge.source); setSymbolKind(null); }} fitView minZoom={0.15} maxZoom={2} nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: false }}><Background variant={BackgroundVariant.Dots} gap={20} size={1} /><Controls showInteractive={false} /></ReactFlow></div><aside className="dependency-details" aria-label="Selected symbol details">{selectedSymbol && selectedEdges.length ? <><span className="eyebrow">SELECTED SYMBOL</span><h2>{selectedSymbol}</h2><p className="dependency-kind">{selectedSymbol.startsWith("file:") ? "Java file" : symbolKind ?? "Java symbol"} · {selectedEdges.length} visible relationships</p><ul>{selectedEdges.map(item => <li key={item.relationship_id}><div><strong>{item.relationship_type}</strong><span className={item.resolution_status === "RESOLVED" ? "dependency-resolved" : "dependency-unresolved"}>{item.resolution_status.toLowerCase()}</span></div><p>{item.source_symbol} → {item.target_symbol}</p><Link href={sourceHref(repositoryId, item)}>Open source · {item.file_path}:{item.start_line}–{item.end_line}<ArrowUpRight size={13} /></Link></li>)}</ul></> : <div className="dependency-details-empty"><Focus size={20} /><strong>Select a symbol</strong><p>Click a graph node to inspect its relationships and open the corresponding source lines.</p></div>}</aside></div>}
    {data && <p className="dependency-limit">Showing {graph.edges.length} of {data.total_relationships} matching relationships{graph.omittedEdges ? ` · ${graph.omittedEdges} omitted by the graph node limit` : ""}. Queries and rendering are capped at {GRAPH_EDGE_LIMIT} relationships; focus on a symbol to inspect its direct neighborhood. Dashed amber edges are unresolved.</p>}
  </div>;
}
