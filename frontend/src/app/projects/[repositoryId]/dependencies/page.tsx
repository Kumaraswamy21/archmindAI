import { DependencyExplorer } from "@/components/dependency-explorer";

export default async function DependenciesPage({ params }: { params: Promise<{ repositoryId: string }> }) {
  const { repositoryId } = await params;
  return <DependencyExplorer key={repositoryId} repositoryId={repositoryId} />;
}
