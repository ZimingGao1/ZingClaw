# ZingClaw Eval Suite Report

## Summary

- Generated at: `2026-07-03T09:10:29.170190+00:00`
- Suite: `D:\Project\ZingClaw\replay_cases\regression`
- Total: `11`
- Passed: `11`
- Failed: `0`
- Pass rate: `100.00%`
- JSON report: `D:\Project\ZingClaw\replay_cases\regression\suite_report.json`

## Failed Cases

No regressions detected.

## Case Details

| Status | Case | Score | Category | Trace |
| --- | --- | ---: | --- | --- |
| PASS | create_and_run_script | 100 | shell | `traces\20260703T090905-33a934d1` |
| PASS | edit_trace_note | 100 | filesystem | `traces\20260703T090913-fd22cbfb` |
| PASS | inspect_then_run | 100 | shell | `traces\20260703T090924-3d4c3df5` |
| PASS | list_after_write | 100 | filesystem | `traces\20260703T090929-c39de75f` |
| PASS | list_workspace | 100 | filesystem | `traces\20260703T090932-241b98d7` |
| PASS | list_workspace_20260703t043656-9fd22f45 | 100 | filesystem | `traces\20260703T090943-6e1c80e6` |
| PASS | python_version | 100 | shell | `traces\20260703T090957-0bdf395e` |
| PASS | read_hello_py | 100 | filesystem | `traces\20260703T091000-9b0b8c15` |
| PASS | run_hello_py | 100 | shell | `traces\20260703T091006-55e85ba3` |
| PASS | safe_shell_listing | 100 | filesystem | `traces\20260703T091009-a22d578b` |
| PASS | write_trace_note | 100 | filesystem | `traces\20260703T091019-01e39350` |

## Case Metadata

### create_and_run_script

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:write_file, writes_files`
- Purpose: Protect shell behavior for: 创建 trace_demo/run_me.py，让它输出 hello trace，然后运行它并总结结果。
- Case file: `replay_cases\regression\create_and_run_script.json`
- Fresh trace: `traces\20260703T090905-33a934d1`

### edit_trace_note

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:read_file, read_or_edit, read_only, semantic_assertion`
- Purpose: Protect trace note normalization: verify notes/trace_demo.md uses 运行轨迹, editing only if needed.
- Case file: `replay_cases\regression\edit_trace_note.json`
- Fresh trace: `traces\20260703T090913-fd22cbfb`

### inspect_then_run

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:read_file`
- Purpose: Protect shell behavior for: 先查看 test/hello.py，再运行它，最后把代码内容和运行结果一起总结。
- Case file: `replay_cases\regression\inspect_then_run.json`
- Fresh trace: `traces\20260703T090924-3d4c3df5`

### list_after_write

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:list_dir, read_only`
- Purpose: Protect filesystem behavior for: 查看 notes 目录，确认 trace demo 文件是否存在。
- Case file: `replay_cases\regression\list_after_write.json`
- Fresh trace: `traces\20260703T090929-c39de75f`

### list_workspace

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, read_only, semantic_assertion`
- Purpose: Protect workspace listing behavior: the agent should inspect the root directory before summarizing it.
- Case file: `replay_cases\regression\list_workspace.json`
- Fresh trace: `traces\20260703T090932-241b98d7`

### list_workspace_20260703t043656-9fd22f45

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:list_dir, read_only`
- Purpose: Protect filesystem behavior for: 查看当前 workspace 的文件结构，并简单说明各目录用途。
- Case file: `replay_cases\regression\list_workspace_20260703t043656-9fd22f45.json`
- Fresh trace: `traces\20260703T090943-6e1c80e6`

### python_version

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec`
- Purpose: Protect shell behavior for: 运行 python --version，并告诉我结果。
- Case file: `replay_cases\regression\python_version.json`
- Fresh trace: `traces\20260703T090957-0bdf395e`

### read_hello_py

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:read_file, read_only`
- Purpose: Protect filesystem behavior for: 读取 test/hello.py 的内容，并解释它做了什么。
- Case file: `replay_cases\regression\read_hello_py.json`
- Fresh trace: `traces\20260703T091000-9b0b8c15`

### run_hello_py

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec`
- Purpose: Protect shell behavior for: 运行 test/hello.py，并告诉我输出是什么。
- Case file: `replay_cases\regression\run_hello_py.json`
- Fresh trace: `traces\20260703T091006-55e85ba3`

### safe_shell_listing

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:list_dir, read_only, safety, semantic_assertion`
- Purpose: Protect safe workspace listing behavior using the safest available local tool.
- Case file: `replay_cases\regression\safe_shell_listing.json`
- Fresh trace: `traces\20260703T091009-a22d578b`

### write_trace_note

- Status: `PASS`
- Score: `100`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:write_file, writes_files`
- Purpose: Protect filesystem behavior for: 创建 notes/trace_demo.md，写入一段关于 trace 作用的简短说明。
- Case file: `replay_cases\regression\write_trace_note.json`
- Fresh trace: `traces\20260703T091019-01e39350`
