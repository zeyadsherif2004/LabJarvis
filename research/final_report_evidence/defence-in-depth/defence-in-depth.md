# Unsafe commands surviving each layer (generated; frozen benchmark labels)

| Configuration | Set | Model output | + validation | + state | + confidence/agreement | + attentive confirmation | if always accepted |
|---|---|---:|---:|---:|---:|---:|---:|
| rules | benchmark | 8 | 0 | 0 | 0 | 0 | 0 |
| rules | heldout | 0 | 0 | 0 | 0 | 0 | 0 |
| qwen2.5-3b-llm | benchmark | 11 | 10 | 10 | 10 | 0 | 10 |
| qwen2.5-3b-llm | heldout | 1 | 1 | 1 | 1 | 0 | 1 |
| qwen2.5-3b-cascade | benchmark | 12 | 4 | 4 | 4 | 0 | 4 |
| qwen2.5-3b-cascade | heldout | 1 | 1 | 1 | 1 | 0 | 1 |
| qwen2.5-1.5b-cascade-verified-qwen2.5-3b | benchmark | 10 | 2 | 2 | 2 | 0 | 2 |
| qwen2.5-1.5b-cascade-verified-qwen2.5-3b | heldout | 0 | 0 | 0 | 0 | 0 | 0 |
| deepseek-flash-cascade | benchmark | 12 | 4 | 4 | 4 | 0 | 4 |
| deepseek-flash-cascade | heldout | 0 | 0 | 0 | 0 | 0 | 0 |

Counts are unsafe *executions* if every gate up to that column applied. 'Attentive confirmation' assumes the operator rejects every wrong proposal - an assumption the user study is designed to test; 'always accepted' is the worst case.
