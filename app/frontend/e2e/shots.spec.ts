import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const OUT = "screenshots";
const WIDTHS = [1440, 375] as const;
const PAGES: readonly { name: string; path: string }[] = [
  { name: "home", path: "/" },
  { name: "runs", path: "/runs" },
  { name: "data", path: "/data" },
  { name: "login", path: "/login" },
  { name: "register", path: "/register" },
];
const PASSWORD = "correct-horse-battery-1";
const FILE = "wdbc.csv";

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

for (const width of WIDTHS) {
  test.describe(`@${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const p of PAGES) {
      test(`shot ${p.name}`, async ({ page }) => {
        await page.goto(p.path);
        await settle(page);
        await page.screenshot({ path: `${OUT}/${p.name}-${width}.png`, fullPage: true });
      });
    }

    test("shot validation errors, ingest form + modal", async ({ page }) => {
      await page.goto("/register");
      await page.getByLabel("이메일").fill("not-an-email");
      await page.getByRole("button", { name: "가입하고 시작하기" }).click();
      await page.screenshot({ path: `${OUT}/register-errors-${width}.png`, fullPage: true });

      // Authenticated data page with a mocked file list (no CSV is staged in data/cache).
      await page.route("**/api/datasets/available", (r) => r.fulfill({ json: [{ file_name: FILE, size_bytes: 123456 }] }));
      const email = `shot-${width}-${Date.now()}@example.com`;
      await page.getByLabel("이메일").fill(email);
      await page.locator("input[name=password]").fill(PASSWORD);
      await page.locator("input[name=confirm]").fill(PASSWORD);
      await page.getByRole("button", { name: "가입하고 시작하기" }).click();
      await expect(page).toHaveURL(/\/data$/);
      await settle(page);
      await page.locator("#ingest-file").selectOption(FILE);
      await page.locator("#ingest-source_url").fill("https://archive.ics.uci.edu/dataset/17");
      await page.getByRole("button", { name: "수집 내용 확인" }).click();
      await page.screenshot({ path: `${OUT}/data-ingest-errors-${width}.png`, fullPage: true });
      await page.locator("#ingest-license").fill("CC BY 4.0");
      await page.getByRole("button", { name: "수집 내용 확인" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.screenshot({ path: `${OUT}/data-ingest-modal-${width}.png` });
    });
  });
}
