import type { components } from "./schema";

export type Repository = components["schemas"]["RepositoryRegistrationResponse"];
export type RepositoryList = components["schemas"]["RepositoryListResponse"];
export type ApprovedAliases = components["schemas"]["ApprovedAliasesResponse"];
export type RegisterRepository = components["schemas"]["RepositoryRegistrationRequest"];
export type IndexStatus = components["schemas"]["IndexStatusResponse"];
export type IndexJob = components["schemas"]["IndexJobResponse"];
export type RepositoryFiles = components["schemas"]["RepositoryFilesResponse"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type ChatResponse = components["schemas"]["ChatResponse"];
export type SourceContent = components["schemas"]["RepositoryFileContentResponse"];
export type DependencyList = components["schemas"]["DependencyListResponse"];
export type DependencyRelationship = components["schemas"]["DependencyRelationship"];
export type JavaSymbols = components["schemas"]["JavaSymbolsResponse"];

export class ApiError extends Error {
  constructor(public readonly code: string, message: string, public readonly requestId?: string) {
    super(message);
    this.name = "ApiError";
  }
}

export async function errorFromResponse(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === "object" && "error" in body) {
    const error = body.error;
    if (error && typeof error === "object" && "code" in error && "message" in error &&
        typeof error.code === "string" && typeof error.message === "string") {
      return new ApiError(error.code, error.message, "request_id" in error && typeof error.request_id === "string" ? error.request_id : undefined);
    }
  }
  return new ApiError("HTTP_ERROR", `The API returned ${response.status}.`);
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch (cause) {
    if (init?.signal?.aborted || (cause instanceof Error && cause.name === "AbortError")) throw cause;
    throw new ApiError("API_UNREACHABLE", "The API is unavailable. Check the backend connection.");
  }
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

export const api = {
  repositories: (limit = 50, offset = 0, signal?: AbortSignal) =>
    apiFetch<RepositoryList>(`/repositories?limit=${limit}&offset=${offset}`, { signal }),
  repository: (id: string, signal?: AbortSignal) =>
    apiFetch<Repository>(`/repositories/${encodeURIComponent(id)}`, { signal }),
  approvedAliases: (signal?: AbortSignal) =>
    apiFetch<ApprovedAliases>("/repositories/approved-aliases", { signal }),
  register: (body: RegisterRepository) =>
    apiFetch<Repository>("/repositories", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  uploadRepository: (name: string, archive: File) =>
    apiFetch<Repository>(`/repositories/upload?name=${encodeURIComponent(name)}`, {
      method: "POST", headers: { "Content-Type": "application/zip" }, body: archive,
    }),
  indexStatus: (id: string, signal?: AbortSignal) =>
    apiFetch<IndexStatus>(`/repositories/${encodeURIComponent(id)}/index/status`, { signal }),
  repositoryFiles: (id: string, signal?: AbortSignal) =>
    apiFetch<RepositoryFiles>(`/repositories/${encodeURIComponent(id)}/files`, { signal }),
  dependencies: (id: string, options: { symbol?: string; relationshipType?: DependencyRelationship["relationship_type"]; limit?: number } = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(options.limit ?? 100) });
    if (options.symbol) params.set("symbol", options.symbol);
    if (options.relationshipType) params.set("relationship_type", options.relationshipType);
    return apiFetch<DependencyList>(`/repositories/${encodeURIComponent(id)}/dependencies?${params}`, { signal });
  },
  javaSymbols: (id: string, filePath: string, signal?: AbortSignal) =>
    apiFetch<JavaSymbols>(`/repositories/${encodeURIComponent(id)}/symbols?file_path=${encodeURIComponent(filePath)}`, { signal }),
  latestIndexJob: (id: string, signal?: AbortSignal) =>
    apiFetch<IndexJob>(`/repositories/${encodeURIComponent(id)}/index/jobs/latest`, { signal }),
  indexJob: (id: string, jobId: string, signal?: AbortSignal) =>
    apiFetch<IndexJob>(`/repositories/${encodeURIComponent(id)}/index/jobs/${encodeURIComponent(jobId)}`, { signal }),
  startIndex: (id: string) =>
    apiFetch<IndexJob>(`/repositories/${encodeURIComponent(id)}/index`, { method: "POST" }),
  chat: (body: ChatRequest, signal?: AbortSignal) =>
    apiFetch<ChatResponse>("/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    }),
  sourceFile: (id: string, path: string, signal?: AbortSignal) =>
    apiFetch<SourceContent>(`/repositories/${encodeURIComponent(id)}/files/content?path=${encodeURIComponent(path)}`, { signal }),
};
