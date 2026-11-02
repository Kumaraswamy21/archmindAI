"use client";

import { useParams } from "next/navigation";
import { ChatWorkspace } from "@/components/chat-workspace";

export default function AiWorkspacePage() {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  return <ChatWorkspace key={repositoryId} repositoryId={repositoryId} />;
}
