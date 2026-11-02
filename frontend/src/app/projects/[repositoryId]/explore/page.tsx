import { SourceBrowser } from "@/components/source-browser";

export default async function ExplorePage({ params, searchParams }: {
  params: Promise<{ repositoryId: string }>;
  searchParams: Promise<{ path?: string | string[]; start_line?: string | string[]; end_line?: string | string[] }>;
}) {
  const { repositoryId } = await params;
  const query = await searchParams;
  const rawPath = typeof query.path === "string" ? query.path : undefined;
  const path = rawPath && !rawPath.startsWith("/") && !rawPath.includes("\\") && !rawPath.split("/").includes("..") && !/^[A-Za-z]:/.test(rawPath) ? rawPath : undefined;
  const start = typeof query.start_line === "string" ? Number(query.start_line) : NaN;
  const end = typeof query.end_line === "string" ? Number(query.end_line) : NaN;
  const highlight = path && Number.isSafeInteger(start) && Number.isSafeInteger(end) && start > 0 && end >= start ? { startLine: start, endLine: end } : undefined;
  return <div className="explore-page"><div className="page-heading"><div><span className="eyebrow with-rule">ARCHMIND / SOURCE EXPLORER</span><h1>Explore source code.</h1><p>Browse files in this approved repository. Source is read only.</p></div></div><SourceBrowser key={`${repositoryId}:${path ?? ""}`} repositoryId={repositoryId} initialPath={path} highlight={highlight} /></div>;
}
