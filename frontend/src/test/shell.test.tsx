import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect } from "vitest";
import { axe } from "vitest-axe";
import { AppShell } from "@/components/app-shell";

vi.mock("next/link", () => ({ default: ({ href, children, ...props }: React.ComponentProps<"a">) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

describe("application navigation", () => {
  it("exposes primary destinations and a skip link target", () => {
    render(<AppShell><h1>Dashboard</h1></AppShell>);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Projects" })).toHaveAttribute("href", "/projects");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("toggles the sidebar and theme with named controls", async () => {
    const user = userEvent.setup();
    const { container } = render(<AppShell><h1>Dashboard</h1></AppShell>);
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.firstChild).toHaveClass("sidebar-collapsed");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
  });

  it("has no obvious accessibility violations in the shell", async () => {
    const { container } = render(<AppShell><h1>Dashboard</h1></AppShell>);
    expect((await axe(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
  });
});
