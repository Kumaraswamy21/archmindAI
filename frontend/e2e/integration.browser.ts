import { expect, test } from "@playwright/test";

const projectA = { repository_id: "11111111-1111-4111-8111-111111111111", name: "Project A", source_alias: "sample-a", status: "REGISTERED", registered_at: "2026-11-01T00:00:00Z" };
const projectB = { ...projectA, repository_id: "22222222-2222-4222-8222-222222222222", name: "Project B", source_alias: "sample-b" };
const apiError = (code: string, message: string) => ({ error: { code, message, request_id: "e2e-request", details: null } });
const ready = { repository_id: projectA.repository_id, index_status: "READY", total_chunks: 1, embedded_chunks: 1 };
const frame = (sequence: number, event_type: string, payload: object) => `id: ${sequence}\nevent: ${event_type}\ndata: ${JSON.stringify({ request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", event_type, sequence, timestamp: "2026-11-01T00:00:00Z", payload })}\n\n`;

test("live approved project journey through indexing, grounded chat, citations, and dependencies", async ({ page }) => {
  test.skip(!process.env.ARCHMIND_E2E_ALIAS, "Set ARCHMIND_E2E_ALIAS to a fresh server-approved sample alias and run the API plus worker.");
  test.setTimeout(300_000);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Understand the system/ })).toBeVisible();
  await page.getByRole("link", { name: "Add a project" }).click();
  await page.getByLabel("Source type").selectOption("approved");
  await page.getByLabel("Display name").fill(`ArchMind browser ${process.env.ARCHMIND_E2E_ALIAS}`);
  await page.getByLabel("Approved source").selectOption(process.env.ARCHMIND_E2E_ALIAS!);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "Start indexing" }).click();
  await expect(page.getByRole("heading", { name: "Indexing completed" })).toBeVisible({ timeout: 180_000 });
  await expect(page.getByRole("link", { name: /Open AI workspace/ })).toBeVisible();
  await page.getByRole("link", { name: /Open AI workspace/ }).click();
  await page.getByRole("textbox", { name: "Ask about this repository" }).fill("How is CommerceService organized and what are its responsibilities?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("Intent classified")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Evidence verified")).toBeVisible({ timeout: 120_000 });
  const firstCitation = page.locator(".chat-citations button").first();
  await expect(firstCitation).toBeVisible({ timeout: 120_000 });
  await firstCitation.click();
  await expect(page.getByRole("dialog", { name: /Source citation/ })).toBeVisible();
  await expect(page.locator('[data-highlighted="true"]').first()).toBeVisible();
  await page.getByRole("button", { name: "Close source citation" }).click();
  await page.getByRole("navigation", { name: "Workspace sections" }).getByRole("link", { name: "Dependencies" }).click();
  await expect(page.locator(".react-flow__edge").first()).toBeAttached();
  await page.locator(".react-flow__node").first().click();
  await expect(page.getByRole("complementary", { name: "Selected symbol details" }).getByRole("link", { name: /Open source/ }).first()).toBeVisible();
});

test("invalid registration and backend unavailable states", async ({ page }) => {
  await page.route("**/api/v1/repositories/approved-aliases", route => route.fulfill({ json: { aliases: ["sample-a"] } }));
  await page.goto("/projects/new");
  await page.getByLabel("Source type").selectOption("approved");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText("Enter a project name.")).toBeVisible();
  await expect(page.getByText("Choose an approved source.")).toBeVisible();
  await page.route("**/api/v1/repositories?*", route => route.fulfill({ status: 503, json: apiError("DATABASE_UNAVAILABLE", "Project listing is unavailable.") }));
  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: "Connection unavailable" })).toBeVisible();
});

test("failed indexing offers a safe retry", async ({ page }) => {
  await page.route(`**/api/v1/repositories/${projectA.repository_id}`, route => route.fulfill({ json: projectA }));
  await page.route(`**/api/v1/repositories/${projectA.repository_id}/files`, route => route.fulfill({ json: { repository_id: projectA.repository_id, total_files: 1, files: [{ path: "src/Service.java", language: "java", size_bytes: 1 }] } }));
  await page.route(`**/api/v1/repositories/${projectA.repository_id}/index/status`, route => route.fulfill({ json: { ...ready, index_status: "FAILED", embedded_chunks: 0 } }));
  await page.route(`**/api/v1/repositories/${projectA.repository_id}/index/jobs/latest`, route => route.fulfill({ json: { job_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", repository_id: projectA.repository_id, status: "FAILED", current_stage: "EMBEDDING", completed_stages: ["DISCOVERING", "PARSING", "CHUNKING", "PERSISTING"], started_at: "2026-11-01T00:00:00Z", completed_at: "2026-11-01T00:01:00Z", error_code: "EMBEDDING_FAILED", error_message: "Embedding provider unavailable." } }));
  await page.goto(`/projects/${projectA.repository_id}`);
  await expect(page.getByRole("heading", { name: "Indexing failed" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry failed indexing" })).toBeEnabled();
});

test("empty retrieval and stream failure are shown without invented citations", async ({ page }) => {
  await page.route(`**/api/v1/repositories/${projectA.repository_id}`, route => route.fulfill({ json: projectA }));
  await page.route(`**/api/v1/repositories/${projectA.repository_id}/index/status`, route => route.fulfill({ json: ready }));
  let attempt = 0;
  await page.route("**/api/v1/chat/stream", route => {
    attempt += 1;
    const body = attempt === 1 ? frame(1, "workflow_started", { repository_id: projectA.repository_id }) + frame(2, "answer_completed", {
      request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", repository_id: projectA.repository_id,
      answer: "No supporting evidence was found.", citations: [], agents_executed: ["code_analysis"], classified_intent: "CODE_LOOKUP", verification_status: "INSUFFICIENT_EVIDENCE", workflow_status: "ANSWERED", execution_duration_ms: 1, prompt_versions: {}, model_identifier: null,
    }) : frame(1, "workflow_started", { repository_id: projectA.repository_id }) + frame(2, "workflow_failed", { code: "PROVIDER_TIMEOUT", message: "Analysis timed out." });
    return route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });
  await page.goto(`/projects/${projectA.repository_id}/ai`);
  const composer = page.getByRole("textbox", { name: "Ask about this repository" });
  await composer.fill("What is unsupported?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("No supporting evidence was found.")).toBeVisible();
  await expect(page.locator(".chat-citations button")).toHaveCount(0);
  await composer.fill("Try another analysis");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("Analysis timed out.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry question" })).toBeEnabled();
});

test("repository switching discards prior chat and source context", async ({ page }) => {
  for (const project of [projectA, projectB]) {
    await page.route(`**/api/v1/repositories/${project.repository_id}`, route => route.fulfill({ json: project }));
    await page.route(`**/api/v1/repositories/${project.repository_id}/index/status`, route => route.fulfill({ json: { ...ready, repository_id: project.repository_id } }));
  }
  await page.route("**/api/v1/repositories?*", route => route.fulfill({ json: { total: 2, limit: 100, offset: 0, repositories: [projectA, projectB] } }));
  await page.route("**/api/v1/chat/stream", route => route.fulfill({ status: 200, contentType: "text/event-stream", body: frame(1, "workflow_started", { repository_id: projectA.repository_id }) + frame(2, "workflow_failed", { code: "TEST_FAILURE", message: "Project A only." }) }));
  await page.goto(`/projects/${projectA.repository_id}/ai`);
  await page.getByRole("textbox", { name: "Ask about this repository" }).fill("Question for A");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByText("Question for A")).toBeVisible();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Projects" }).click();
  await page.getByRole("link", { name: /Open Project B workspace/ }).click();
  await page.getByRole("navigation", { name: "Workspace sections" }).getByRole("link", { name: "AI workspace" }).click();
  await expect(page.getByText("Question for A")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Project B" })).toBeVisible();
});
