import { describe, expect, it } from "vitest";

import { editorAgentSettings } from "./progress";

describe("editorAgentSettings", () => {
  it("WebUI の中継の URL、合言葉、モデルを並べる", () => {
    expect(editorAgentSettings("http://192.168.1.10:8000", "qwen3-8b", "qwen3:8b", "合言葉")).toBe(
      ["Base URL: http://192.168.1.10:8000/api/environments/qwen3-8b/openai/v1", "API Key:  合言葉", "Model ID: qwen3:8b"].join("\n"),
    );
  });
});
