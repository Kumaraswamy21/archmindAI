import type { Edge, Node } from "@xyflow/react";
import type { DependencyRelationship } from "@/lib/api/client";

export const GRAPH_EDGE_LIMIT = 100;
export const GRAPH_NODE_LIMIT = 70;

export type GraphNode = Node<{ label: string; symbol: string }>;
export type GraphEdge = Edge<{ relationship: DependencyRelationship }>;

export function mapDependencyGraph(relationships: DependencyRelationship[], repositoryId: string, maxNodes = GRAPH_NODE_LIMIT) {
  const symbols = new Set<string>();
  const kept: DependencyRelationship[] = [];
  for (const relationship of relationships) {
    if (relationship.repository_id !== repositoryId) continue;
    const additions = Number(!symbols.has(relationship.source_symbol)) + Number(!symbols.has(relationship.target_symbol) && relationship.target_symbol !== relationship.source_symbol);
    if (symbols.size + additions > maxNodes) continue;
    symbols.add(relationship.source_symbol);
    symbols.add(relationship.target_symbol);
    kept.push(relationship);
  }
  const sorted = [...symbols].sort((a, b) => a.localeCompare(b));
  const positions = new Map(sorted.map((symbol, index) => [symbol, { x: (index % 5) * 235, y: Math.floor(index / 5) * 125 }]));
  const nodes: GraphNode[] = sorted.map(symbol => ({
    id: symbol,
    position: positions.get(symbol)!,
    data: { symbol, label: symbol.startsWith("file:") ? symbol.slice(5).split("/").at(-1)! : symbol.split(".").at(-1)! },
    draggable: false,
    className: symbol.startsWith("file:") ? "dependency-file-node" : "dependency-symbol-node",
  }));
  const edges: GraphEdge[] = kept.map(relationship => ({
    id: relationship.relationship_id,
    source: relationship.source_symbol,
    target: relationship.target_symbol,
    label: relationship.relationship_type,
    type: "smoothstep",
    animated: false,
    data: { relationship },
    className: relationship.resolution_status === "UNRESOLVED" ? "dependency-edge-unresolved" : "dependency-edge-resolved",
    style: { stroke: relationship.resolution_status === "UNRESOLVED" ? "#d7a372" : "#55ddbd", strokeWidth: 1.5, strokeDasharray: relationship.resolution_status === "UNRESOLVED" ? "5 4" : undefined },
    labelStyle: { fill: "#9aa9a9", fontSize: 9, fontWeight: 700 },
    labelBgStyle: { fill: "#111c22" },
    labelBgPadding: [4, 2],
    labelBgBorderRadius: 3,
  }));
  return { nodes, edges, omittedEdges: relationships.length - kept.length };
}
