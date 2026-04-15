import { expect, test } from "@playwright/test";

test.describe("契約／E2E（選修）", () => {
  test("後端 /health 可連線", async ({ request }) => {
    const res = await request.get("http://127.0.0.1:8000/health");
    expect(res.ok()).toBeTruthy();
    const body = (await res.json()) as { status?: string };
    expect(body.status).toBe("ok");
  });

  test("對話頁：mock POST /api/v1/chat 回覆可顯示", async ({ page }) => {
    await page.route("**/api/v1/chat", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          answer: "契約測試 mock 回覆",
          sources: [],
          chunks: [],
          tool_name: "rag_search",
          extra: null,
          latency_sec: 0.01,
          next_original_question_for_clarification: null,
          next_chart_confirmation_question: null,
        }),
      });
    });

    await page.goto("/chat");
    await page.getByLabel("輸入訊息").fill("你好");
    await page.getByRole("button", { name: "送出" }).click();
    await expect(page.getByText("契約測試 mock 回覆")).toBeVisible();
  });
});
