import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect } from "vitest";
import { axe } from "vitest-axe";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

vi.mock("next/link", () => ({ default: ({ href, children, ...props }: React.ComponentProps<"a">) => <a href={href} {...props}>{children}</a> }));

describe("feedback states", () => {
  it("announces loading and offers a retry on errors", async () => {
    const retry = vi.fn();
    const { rerender } = render(<LoadingState />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading projects");
    rerender(<ErrorState message="API unavailable" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("API unavailable");
    await userEvent.setup().click(screen.getByRole("button", { name: /Try again/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("provides a clear empty state and accessible action", async () => {
    const { container } = render(<EmptyState />);
    expect(screen.getByRole("link", { name: /Add your first project/ })).toHaveAttribute("href", "/projects/new");
    expect((await axe(container, { rules: { "color-contrast": { enabled: false } } })).violations).toEqual([]);
  });
});
