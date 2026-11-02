import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DependencyExplorer } from "@/components/dependency-explorer";
import { GRAPH_NODE_LIMIT, mapDependencyGraph } from "@/lib/dependency-graph";
import type { DependencyRelationship } from "@/lib/api/client";

const repositoryId = "11111111-1111-4111-8111-111111111111";
const otherId = "22222222-2222-4222-8222-222222222222";
const relationship = (overrides: Partial<DependencyRelationship> = {}): DependencyRelationship => ({
  relationship_id: "edge-1", repository_id: repositoryId, source_symbol: "demo.Service", target_symbol: "demo.Repository",
  relationship_type: "INJECTS", file_path: "src/Service.java", start_line: 4, end_line: 5,
  resolution_status: "RESOLVED", ...overrides,
});
const rows = [relationship(), relationship({ relationship_id: "edge-2", source_symbol: "demo.Service.run", target_symbol: "save", relationship_type: "CALLS", start_line: 9, end_line: 9, resolution_status: "UNRESOLVED" })];
const response = (relationships: DependencyRelationship[], total = relationships.length) => new Response(JSON.stringify({ repository_id: repositoryId, total_relationships: total, relationships }), { status: 200 });

vi.mock("@xyflow/react", async importOriginal => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return { ...actual, ReactFlow: ({ nodes, edges, onNodeClick, children }: {
    nodes: Array<{ id: string; data: { label: string } }>;
    edges: Array<{ id: string; label: string }>;
    onNodeClick: (event: React.MouseEvent, node: { id: string }) => void;
    children: React.ReactNode;
  }) => <div data-testid="graph">{nodes.map(node => <button type="button" key={node.id} onClick={event => onNodeClick(event, node)}>{node.data.label}</button>)}{edges.map(edge => <span key={edge.id} data-testid="graph-edge">{edge.label}</span>)}{children}</div>, Background: () => null, Controls: () => null };
});
afterEach(() => vi.unstubAllGlobals());

it("maps exact dependency nodes and edges without inventing cross-repository relationships", () => {
  const result = mapDependencyGraph([...rows, relationship({ relationship_id: "foreign", repository_id: otherId })], repositoryId);
  expect(result.nodes.map(node => node.id)).toEqual(["demo.Repository", "demo.Service", "demo.Service.run", "save"]);
  expect(result.edges.map(edge => [edge.source, edge.target, edge.label])).toEqual([["demo.Service", "demo.Repository", "INJECTS"], ["demo.Service.run", "save", "CALLS"]]);
  expect(result.edges[1].style?.strokeDasharray).toBe("5 4");
  expect(result.edges.some(edge => edge.id === "foreign")).toBe(false);
});

it("caps graph rendering even when the API returns many relationships", () => {
  const many = Array.from({ length: 100 }, (_, index) => relationship({ relationship_id: `edge-${index}`, source_symbol: `demo.Source${index}`, target_symbol: `demo.Target${index}` }));
  const result = mapDependencyGraph(many, repositoryId);
  expect(result.nodes.length).toBeLessThanOrEqual(GRAPH_NODE_LIMIT);
  expect(result.edges).toHaveLength(GRAPH_NODE_LIMIT / 2);
  expect(result.omittedEdges).toBe(100 - GRAPH_NODE_LIMIT / 2);
});

it("loads graph, filters resolution and relationship type, and shows source navigation for a selected symbol", async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.includes("/symbols?")) return Promise.resolve(new Response(JSON.stringify({ file_path: "src/Service.java", package: "demo", imports: [], symbols: [{ name: "Service", kind: "class", qualified_name: "demo.Service", enclosing_type: null, annotations: [], start_line: 1, end_line: 12 }] }), { status: 200 }));
    if (url.includes("relationship_type=CALLS")) return Promise.resolve(response([rows[1]], 1));
    if (url.includes("/dependencies?")) return Promise.resolve(response(rows));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DependencyExplorer repositoryId={repositoryId} />);
  expect(await screen.findByTestId("graph")).toBeInTheDocument();
  expect(screen.getAllByTestId("graph-edge")).toHaveLength(2);
  await userEvent.setup().click(screen.getByRole("button", { name: "Service" }));
  expect(await screen.findByText(/class · 1 visible relationships/i)).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /Open source/ });
  expect(link.getAttribute("href")).toContain(`/projects/${repositoryId}/explore?`);
  expect(link.getAttribute("href")).toContain("path=src%2FService.java");
  expect(link.getAttribute("href")).toContain("start_line=4");
  fireEvent.change(screen.getByRole("combobox", { name: "Filter resolution status" }), { target: { value: "UNRESOLVED" } });
  expect(screen.getAllByTestId("graph-edge")).toHaveLength(1);
  fireEvent.change(screen.getByRole("combobox", { name: "Filter relationship type" }), { target: { value: "CALLS" } });
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes("relationship_type=CALLS"))).toBe(true));
});

it("searches loaded symbols and focuses an exact symbol through the backend", async () => {
  const fetcher = vi.fn((url: string) => {
    if (url.includes("/symbols?")) return Promise.resolve(new Response(JSON.stringify({ file_path: "src/Service.java", symbols: [] }), { status: 200 }));
    if (url.includes("/dependencies?")) return Promise.resolve(response(rows));
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DependencyExplorer repositoryId={repositoryId} />);
  await screen.findByTestId("graph");
  await userEvent.setup().type(screen.getByRole("textbox", { name: "Search symbols" }), "Repository");
  await userEvent.setup().click(screen.getByRole("button", { name: /demo.Repository/ }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes("symbol=demo.Repository"))).toBe(true));
  expect(screen.getByText(/Focused on/)).toBeInTheDocument();
});

it("shows empty states and rejects a foreign repository response", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response([], 0)).mockResolvedValueOnce(new Response(JSON.stringify({ repository_id: otherId, total_relationships: 1, relationships: rows }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<DependencyExplorer repositoryId={repositoryId} />);
  expect(await screen.findByText("No dependencies found")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Filter relationship type" }), { target: { value: "CALLS" } });
  expect(await screen.findByText("Dependencies unavailable")).toBeInTheDocument();
  expect(screen.getByText(/did not match the selected repository/)).toBeInTheDocument();
});
