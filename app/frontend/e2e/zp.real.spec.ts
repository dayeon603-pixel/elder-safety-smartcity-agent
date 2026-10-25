import { test } from "@playwright/test";
import { stubShell, switchToPohang, walkAed, walkBriefingDefault, walkSilver } from "./zpFlow";

/** Against the live API (make dev). Skips itself while the module tables are empty (`make modules-load`). */
test.beforeEach(async ({ page, request }) => {
  const probe = await request.get("/api/silver/summary");
  test.skip(!probe.ok(), `module data not loaded (HTTP ${probe.status()}); run make modules-load`);
  const heat = await request.get("/api/briefing/11620?date=2025-07-22&teams=10");
  test.skip(!heat.ok(), `briefing not available (HTTP ${heat.status()})`);
  await stubShell(page);
});

test("real: briefing 관악구 then 포항시 남구", async ({ page }) => {
  await walkBriefingDefault(page);
  await switchToPohang(page);
});

test("real: silver", async ({ page }) => walkSilver(page));
test("real: aed", async ({ page }) => walkAed(page));
