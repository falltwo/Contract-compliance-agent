import { expect, test } from "@playwright/test";

test.describe("health + chat mock E2E", () => {
  test("/health returns ok", async ({ request }) => {
    const res = await request.get("http://127.0.0.1:8000/health");
    expect(res.ok()).toBeTruthy();
    const body = (await res.json()) as { status?: string };
    // "degraded" 表示 API 本身運作，但某些依賴（Ollama/Pinecone）不可達（如 CI 環境）
    expect(["ok", "degraded"]).toContain(body.status);
  });

  test("chat page renders mock POST /api/v1/chat/stream SSE response", async ({ page }) => {
    // Mock the SSE streaming endpoint used by the frontend
    await page.route("**/api/v1/chat/stream", async (route) => {
      const sseBody = [
        'event: status\ndata: {"stage": "routing", "message": "正在分析問題類型..."}\n\n',
        'event: token\ndata: {"t": "mock answer visible"}\n\n',
        'event: meta\ndata: {"sources": [], "chunks": [], "tool_name": "rag_search", "extra": null, "latency_sec": 0.01, "next_original_question_for_clarification": null, "next_chart_confirmation_question": null}\n\n',
        'event: done\ndata: {}\n\n',
      ].join("");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sseBody,
      });
    });

    await page.goto("/chat");
    await page.getByRole("button", { name: "Legal Assistant" }).click();
    await page.getByTestId("chat-input").fill("hello");
    await page.getByTestId("chat-send").click();
    await expect(page.getByText("mock answer visible")).toBeVisible();
  });
});
