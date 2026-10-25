import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const PASSWORD = "correct-horse-battery-1";

function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function fillAuth(page: Page, email: string, password: string, confirm?: string): Promise<void> {
  await page.getByLabel("이메일").fill(email);
  await page.locator("input[name=password]").fill(password);
  if (confirm !== undefined) await page.locator("input[name=confirm]").fill(confirm);
}

test.beforeAll(async ({ request }) => {
  const res = await request.get("/api/runs/comparison");
  expect(res.ok(), "backend reachable through the dev proxy").toBeTruthy();
  const runs = (await res.json()) as unknown[];
  expect(runs.length, "no experiment in the DB: run `make exp` once").toBeGreaterThanOrEqual(2);
});

test("register, logout, login, view comparison", async ({ page }) => {
  const email = uniqueEmail();

  // Register: client-side validation first.
  await page.goto("/register");
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page.getByText("이메일을 입력해 주세요.")).toBeVisible();
  await fillAuth(page, email, "short", "short");
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page.getByText(/12자 이상이어야 합니다/)).toBeVisible();
  await fillAuth(page, email, PASSWORD, "different-password-1");
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page.getByText("비밀번호가 일치하지 않습니다.")).toBeVisible();

  await fillAuth(page, email, PASSWORD, PASSWORD);
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();

  // Token survives a reload within the tab (sessionStorage), and logout clears it.
  await page.reload();
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page.getByRole("link", { name: "로그인" }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "로그아웃" })).toHaveCount(0);

  // Login: wrong password -> server error, right password -> in.
  await page.goto("/login");
  await fillAuth(page, email, "wrong-password-123");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("이메일 또는 비밀번호가 올바르지 않습니다.");
  await fillAuth(page, email, PASSWORD);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();

  // View comparison.
  await page.goto("/runs"); // template experiment page: kept as a route, hidden from the nav
  await expect(page).toHaveURL(/\/runs$/);
  await expect(page.getByRole("heading", { name: "기준선 대비 성능" })).toBeVisible();
  const table = page.getByRole("region", { name: "지표별 비교표" });
  await expect(table.getByRole("row", { name: /AUROC/ })).toBeVisible();
  await expect(table.getByText("유의한 개선").first()).toBeVisible();
  await expect(page.getByText(/SHA-256/).first()).toBeVisible();
  await expect(page.locator(".recharts-bar-rectangle").first()).toBeVisible();
});

test("registering a taken email gives the same neutral outcome (no account probing)", async ({ page }) => {
  const email = uniqueEmail();
  await page.goto("/register");
  await fillAuth(page, email, PASSWORD, PASSWORD);
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
  await page.getByRole("button", { name: "로그아웃" }).click();
  await page.goto("/register");
  const other = "another-password-99";
  await fillAuth(page, email, other, other);
  await page.getByRole("button", { name: "가입하고 시작하기" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "로그아웃" })).toHaveCount(0);
  await expect(page.getByText(/가입 요청이 처리되었습니다/)).toBeVisible();
});
