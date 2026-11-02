import { ProjectWorkspace } from "@/components/project-workspace";

export default async function WorkspaceLayout({ children, params }: {
  children: React.ReactNode;
  params: Promise<{ repositoryId: string }>;
}) {
  const { repositoryId } = await params;
  return <ProjectWorkspace key={repositoryId}>{children}</ProjectWorkspace>;
}
