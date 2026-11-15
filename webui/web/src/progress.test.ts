import { describe, expect, it } from "vitest";

import { editorAgentSettings, stageLabelKey } from "./progress";

describe("editorAgentSettings", () => {
  it("WebUI の中継の URL、合言葉、モデルを並べる", () => {
    expect(editorAgentSettings("http://192.168.1.10:8000", "qwen3-8b", "qwen3:8b", "合言葉")).toBe(
      ["Base URL: http://192.168.1.10:8000/api/environments/qwen3-8b/openai/v1", "API Key:  合言葉", "Model ID: qwen3:8b"].join("\n"),
    );
  });
});

describe("stageLabelKey", () => {
  it("vLLM では導入の段階の名前だけを変える", () => {
    expect(stageLabelKey("installing_ollama", "vllm")).toBe("progress.stages.installing_vllm");
    expect(stageLabelKey("installing_ollama", "ollama")).toBe("progress.stages.installing_ollama");
    expect(stageLabelKey("pulling_model", "vllm")).toBe("progress.stages.pulling_model");
  });
});
