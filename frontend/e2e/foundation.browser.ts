import { expect, test } from "@playwright/test";

const project = {
  repository_id: "91e6b3ac-5520-48a9-88e2-22ae25433122",
  name: "Customer Portal",
  source_alias: "customer-portal",
  status: "REGISTERED",
  registered_at: "2026-11-01T00:00:00Z",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/repositories?*", route => route.fulfill({ json: {
    total: 1, limit: 50, offset: 0, repositories: [project],
  } }));
  await page.route("**/api/v1/repositories/approved-aliases", route => route.fulfill({ json: { aliases: ["customer-portal"] } }));
  await page.route(`**/api/v1/repositories/${project.repository_id}`, route => route.fulfill({ json: project }));
});

test("dashboard and project workspace render with working navigation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Understand the system/ })).toBeVisible();
  await page.screenshot({ path: "/private/tmp/archmind-dashboard.png", fullPage: true });
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toBeVisible();
  await page.getByRole("link", { name: /Open Customer Portal workspace/ }).click();
  await expect(page.getByRole("heading", { name: "Customer Portal" })).toBeVisible();
  await page.getByRole("navigation", { name: "Workspace sections" }).getByRole("link", { name: "Code explorer" }).click();
  await expect(page.getByRole("heading", { name: "Explore source code." })).toBeVisible();
});

test("mobile navigation and theme are usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.screenshot({ path: "/private/tmp/archmind-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).last().click();
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.screenshot({ path: "/private/tmp/archmind-mobile-light.png", fullPage: true });
});

test("approved aliases populate project registration", async ({ page }) => {
  await page.goto("/projects/new");
  await expect(page.getByRole("heading", { name: "Add a project" })).toBeVisible();
  await page.getByLabel("Source type").selectOption("approved");
  await expect(page.getByRole("option", { name: "customer-portal" })).toBeAttached();
  await page.getByLabel("Display name").fill("New workspace");
  await page.getByLabel("Approved source").selectOption("customer-portal");
  await expect(page.getByRole("button", { name: /Create project/ })).toBeEnabled();
});

test("ZIP upload is available with no approved aliases", async ({ page }) => {
  await page.route("**/api/v1/repositories/approved-aliases", route => route.fulfill({ json: { aliases: [] } }));
  await page.route("**/api/v1/repositories/upload?*", route => route.fulfill({ status: 201, json: project }));
  await page.goto("/projects/new");
  await page.getByLabel("Display name").fill("Customer Portal");
  await page.getByLabel("Repository ZIP").setInputFiles({ name: "source.zip", mimeType: "application/zip", buffer: Buffer.from("ZIP") });
  await page.getByRole("button", { name: /Create project/ }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project.repository_id}$`));
});
