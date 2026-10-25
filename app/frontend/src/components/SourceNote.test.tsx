import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Dataset } from "../api/types";
import { SourceNote } from "./SourceNote";

const DS: Dataset = {
  id: 1, name: "wdbc", source_url: "https://archive.ics.uci.edu/dataset/17", license: "CC BY 4.0",
  sha256: "0123456789abcdef".repeat(4), fetched_at: "2026-10-23T17:38:25+00:00", file_name: null, row_count: 569, description: "",
};

afterEach(() => vi.unstubAllGlobals());

describe("SourceNote", () => {
  it("renders publisher, license, row count and a 12-char hash with the full hash on hover", () => {
    render(<SourceNote dataset={DS} />);
    expect(screen.getByText(/archive.ics.uci.edu/)).toBeInTheDocument();
    expect(screen.getByText(/CC BY 4.0/)).toBeInTheDocument();
    expect(screen.getByText(/569행/)).toBeInTheDocument();
    const code = screen.getByText("0123456789ab");
    expect(code).toHaveAttribute("title", DS.sha256);
    expect(screen.getByRole("link", { name: "wdbc" })).toHaveAttribute("rel", "noreferrer noopener");
  });

  it("looks the dataset up by id from /api/datasets", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([DS]), { status: 200 })));
    render(<SourceNote datasetId={1} />);
    expect(await screen.findByText(/CC BY 4.0/)).toBeInTheDocument();
  });

  it("says so when the run has no linked dataset", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([DS]), { status: 200 })));
    render(<SourceNote datasetId={99} />);
    expect(await screen.findByText(/기록되어 있지 않습니다/)).toBeInTheDocument();
  });
});
