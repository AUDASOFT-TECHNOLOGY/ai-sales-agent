import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TimeChart } from "./TimeChart";

const series = [
  { id: "a", label: "Billed", color: "var(--dv-billed)", values: [10, null, 30] },
  { id: "b", label: "Collected", color: "var(--dv-collected)", values: [5, 6, null] },
];

describe("TimeChart", () => {
  it("draws a legend, a text alternative and a data table", () => {
    const { container } = render(<TimeChart title="Billed vs collected" labels={["2026-01", "2026-02", "2026-03"]} series={series} />);
    expect(screen.getByRole("img", { name: /Billed vs collected/ })).toBeInTheDocument();
    const legend = screen.getByRole("list", { name: /legend/ });
    expect(within(legend).getByText("Billed")).toBeInTheDocument();
    expect(within(legend).getByText("Collected")).toBeInTheDocument();
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("never turns an unknown value into a zero", () => {
    const { container } = render(<TimeChart title="t" labels={["a", "b", "c"]} series={series} />);
    // 4 known values -> 4 markers' titles; the two gaps have none.
    const tips = [...container.querySelectorAll("svg.dv-svg title")].map((t) => t.textContent);
    expect(tips.filter((t) => t?.includes("-"))).toHaveLength(4);
    const cells = [...container.querySelectorAll("tbody tr")].map((r) => r.textContent);
    expect(cells[1]).toContain("-"); // the table shows a dash, not 0
    expect(cells.join(" ")).not.toMatch(/\b0\b/);
  });

  it("says why instead of drawing when the figure is not tracked", () => {
    render(<TimeChart title="Costs" labels={[]} series={[]} unavailable="no price provided" />);
    expect(screen.getByRole("status")).toHaveTextContent("Not tracked: no price provided");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shows an empty state when there is nothing to draw", () => {
    render(<TimeChart title="Costs" labels={["a"]} series={[{ id: "x", label: "X", color: "red", values: [null] }]} emptyText="Nothing here." />);
    expect(screen.getByRole("status")).toHaveTextContent("Nothing here.");
  });
});
