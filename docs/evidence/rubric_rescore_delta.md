# Rubric vs decomposed rescore delta

| verdict | decomposed_ipi | rubric_ipi |
|---|---|---|
| success | 0 | 0 |
| partial | 0 | 0 |
| refused | 0 | 0 |
| error | 602 | 602 |

Run id `run-30b6a74df9f3`, 611 traces (602 non-empty, 9 empty). The
exfiltration-class change could not be measured: both judges are LLM judges over
the `CLAUDE_JUDGE` role and errored identically on `RuntimeError:
AnthropicProvider needs 'ANTHROPIC_API_KEY' in the environment (set api_key_env in
the provider config to override)`, so 0/611 scored in each dry run and no verdict
distribution was produced.
