# Reasoning Extraction Modes

This file records response shapes, not model-specific behavior. Model entries
in `API_config/config.json` choose one of these modes through
`reasoning_extract_mode`.

| mode | reasoning source | final content rule | dangling support | notes |
|---|---|---|---|---|
| `none` | none | `message.content` | no | Use for non-reasoning models or unsupported APIs. |
| `auto` | ordered fallback | Mode dependent | yes | Tries separated fields first, then content tags. |
| `field_reasoning_content` | `message.reasoning_content` | `message.content` | no | OpenAI-compatible separated reasoning field. |
| `field_reasoning` | `message.reasoning` | `message.content` | no | Possible vLLM reasoning-parser output. |
| `field_reasoning_details` | `message.reasoning_details` | `message.content` | no | Some gateways expose structured reasoning details. |
| `content_think_tags` | `message.content` `<think>...</think>` block | Remove the tag block from `content` | yes | Useful for template-level thinking embedded in content. |
| `content_redacted_thinking_tags` | `message.content` `<redacted_thinking>...</redacted_thinking>` block | Remove the tag block from `content` | yes | Useful for gateways that rename thinking tags. |

## Current Project Mapping

| model family / state | suggested mode | request body notes |
|---|---|---|
| DeepSeek thinking on | `field_reasoning_content` | `{"thinking": {"type": "enabled"}}` |
| DeepSeek thinking off | `none` | `{"thinking": {"type": "disabled"}}` |
| Kimi thinking on | `field_reasoning_content` | `{"thinking": {"type": "enabled"}, "chat_template_kwargs": {"thinking": true}}` |
| Kimi thinking off | `none` | `{"thinking": {"type": "disabled"}, "chat_template_kwargs": {"thinking": false}}` |
| Qwen3 thinking on through vLLM without `--reasoning-parser` | `content_think_tags` | `{"chat_template_kwargs": {"enable_thinking": true}}` |
| Qwen3 thinking off | `none` | `{"chat_template_kwargs": {"enable_thinking": false}}` |

## Probe Workflow

Use `probe_reasoning_extraction.py` to test a configured `model_key`:

```powershell
python API_Tools\probe_reasoning_extraction.py `
  --model-key Qwen3-8b_think `
  --prompt "Which is larger, 9.11 or 9.8? Answer shortly." `
  --mode auto
```

If the detected shape is new, add a new mode here first, then implement it in
`reasoning_extractor.py`, and finally point model configs at that mode.
