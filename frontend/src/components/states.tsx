import Link from "next/link";
import { AlertCircle, ArrowRight, FolderGit2, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "Loading projects" }: { label?: string }) {
  return <div className="state-panel" role="status" aria-live="polite"><LoaderCircle className="state-icon spin" size={24} /><h2>{label}</h2><p>Reading the latest project metadata from the API.</p></div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="state-panel state-error" role="alert"><AlertCircle className="state-icon" size={25} /><h2>Connection unavailable</h2><p>{message}</p>{onRetry && <button type="button" className="button button-secondary" onClick={onRetry}>Try again <ArrowRight size={16} /></button>}</div>;
}

export function EmptyState({ title = "No projects yet", description = "Add an approved repository to begin building a map of your codebase.", action = true }: { title?: string; description?: string; action?: boolean }) {
  return <div className="state-panel state-empty"><FolderGit2 className="state-icon" size={27} /><h2>{title}</h2><p>{description}</p>{action && <Link href="/projects/new" className="button button-primary">Add your first project <ArrowRight size={16} /></Link>}</div>;
}

