import type { Page } from "@playwright/test";
import { stubShell } from "./heatFlow";

const AGENT_STUB_DELAY_MS = 600;

/** Deterministic /api/heat responses for the field page; the crosswalk and AED JSON are the real bundled files. */
export async function stubField(page: Page, agent: "missing" | "ok" = "missing"): Promise<void> {
  await stubShell(page);
  await page.route("**/api/heat/meta", (r) => r.fulfill({ json: { years: [2024, 2025], variants: ["F", "O"], methods: ["B1", "OURS"], season: "05-20~09-30", sources: [] } }));
  await page.route("**/api/heat/days**", (r) => r.fulfill({ json: [{ date: "2025-08-15", n_cases: 79, k_budget: 40, b1_captured: 14, ours_captured: 47 }, { date: "2025-07-22", n_cases: 147, k_budget: 42, b1_captured: 26, ours_captured: 63 }] }));
  const regions = [
    { code: "11590", sido: "서울특별시", name: "동작구", lat: 37.5124, lon: 126.9393, pop65: 60000 },
    { code: "41150", sido: "경기도", name: "의정부시", lat: 37.738, lon: 127.0337, pop65: 70000 },
    { code: "46130", sido: "전라남도", name: "여수시", lat: 34.76, lon: 127.66, pop65: 60000 },
  ];
  await page.route("**/api/heat/regions", (r) => r.fulfill({ json: regions }));
  await page.route("**/api/heat/region/**", (r) => {
    const code = r.request().url().split("/region/")[1]?.split("?")[0] ?? "";
    const region = regions.find((x) => x.code === code) ?? regions[0];
    return r.fulfill({ json: { region, cases_today: 3, scores: { b1: 1, ours: 1, rank: 1 }, drivers: { tmax: 35.2, hist_rate: 0.1, old_share: 0.2 }, shelters: { coverage: "none", nearby: [] } } });
  });
  if (agent === "missing") {
    await page.route("**/api/agent/run", (r) => r.fulfill({ status: 404, json: { detail: "Not Found" } }));
  } else {
    await page.route("**/api/agent/run", async (r) => {
      await new Promise((res) => setTimeout(res, AGENT_STUB_DELAY_MS));
      await r.fulfill({
        json: {
          run_id: "run-0123456789ab", trigger: "fall", planner: "llm", planner_reason: "", notes: [],
          model_name: "stub-model", model_digest: "deadbeef",
          steps: [
            { n: 1, tool: "get_heat_region", source: "llm", ok: true, ms: 1.5, summary: "stage=warning", result: {} },
            { n: 2, tool: "send_119_report", source: "llm", ok: true, ms: 2.5, summary: "receipt", result: { simulator: "119 시뮬레이터", http_status: 202, receipt_id: "emg-0123456789ab" } },
            { n: 3, tool: "describe_scene", source: "llm", ok: true, ms: 9, summary: "scene", result: { description: "stub scene" } },
          ],
          briefing: "stub briefing", briefing_source: "llm", grounding: { checked: true, ok: true, reasons: [] }, total_ms: 5,
        },
      });
    });
  }
}
