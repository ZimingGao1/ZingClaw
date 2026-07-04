# ZingClaw Eval Suite Report

## Summary

- Generated at: `2026-07-04T06:36:09.088051+00:00`
- Suite: `D:\Project\ZingClaw\replay_cases_challenge\regression`
- Total: `6`
- Passed: `3`
- Failed: `3`
- Pass rate: `50.00%`
- JSON report: `D:\Project\ZingClaw\replay_cases_challenge\regression\suite_report.json`

## Failed Cases

### challenge_edit_existing_note

- Score: `88`
- Category: `filesystem`
- Trace: `traces\20260704T063450-b3282bf5`
- Failed checks:
  - expected_tool:edit_file: observed tools: read_file

### challenge_json_write_parse

- Score: `67`
- Category: `shell`
- Trace: `traces\20260704T063510-ebb11cc2`
- Failed checks:
  - expected_tool:write_file: observed tools: exec
  - expected_command:python -c "import json; data = json.load(open('challenge/config.json')); print(data['name'])": observed commands: ['python -c "import json; f=open(\'challenge/config.json\'); d=json.load(f); print(d[\'name\'])"']
  - expected_file_touched:challenge/config.json: observed files: ['(none)']

### challenge_workspace_summary_from_tools

- Score: `88`
- Category: `filesystem`
- Trace: `traces\20260704T063546-ced0a554`
- Failed checks:
  - expected_file_touched:challenge/e2e_note.md: observed files: ['.', 'challenge', 'challenge/sum_numbers.py', 'cron', 'notes', 'test', 'test/hello.py', 'trace_demo', 'trace_demo/run_me.py']
  - expected_file_touched:challenge/config.json: observed files: ['.', 'challenge', 'challenge/sum_numbers.py', 'cron', 'notes', 'test', 'test/hello.py', 'trace_demo', 'trace_demo/run_me.py']

## Case Details

| Status | Case | Score | Category | Trace |
| --- | --- | ---: | --- | --- |
| FAIL | challenge_edit_existing_note | 88 | filesystem | `traces\20260704T063450-b3282bf5` |
| FAIL | challenge_json_write_parse | 67 | shell | `traces\20260704T063510-ebb11cc2` |
| PASS | challenge_read_then_run | 100 | shell | `traces\20260704T063515-d2952ec4` |
| PASS | challenge_script_create_run | 100 | shell | `traces\20260704T063524-10eef9ec` |
| PASS | challenge_tool_error_then_shell | 100 | shell | `traces\20260704T063532-126b942c` |
| FAIL | challenge_workspace_summary_from_tools | 88 | filesystem | `traces\20260704T063546-ced0a554` |

## Case Metadata

### challenge_edit_existing_note

- Status: `FAIL`
- Score: `88`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:edit_file, tool:read_file, writes_files`
- Purpose: Protect filesystem behavior for: 读取 notes/trace_demo.md，如果里面没有“回归验证”四个字，就追加一行“回归验证：用于检查修改后能力是否退化。”；最后说明你是否修改了文件。
- Case file: `replay_cases_challenge\regression\challenge_edit_existing_note.json`
- Fresh trace: `traces\20260704T063450-b3282bf5`

### challenge_json_write_parse

- Status: `FAIL`
- Score: `67`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:write_file, writes_files`
- Purpose: Protect shell behavior for: 创建 challenge/config.json，写入 {"name":"zingclaw","mode":"eval","enabled":true}；然后用 Python 命令读取并打印 name 字段，告诉我输出。
- Case file: `replay_cases_challenge\regression\challenge_json_write_parse.json`
- Fresh trace: `traces\20260704T063510-ebb11cc2`

### challenge_read_then_run

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:read_file`
- Purpose: Protect shell behavior for: 先读取 trace_demo/run_me.py 的内容，再运行它，最后同时说明代码内容和运行输出是否一致。
- Case file: `replay_cases_challenge\regression\challenge_read_then_run.json`
- Fresh trace: `traces\20260704T063515-d2952ec4`

### challenge_script_create_run

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:write_file, writes_files`
- Purpose: Protect shell behavior for: 创建 challenge/sum_numbers.py，内容是计算 [1, 2, 3, 4] 的和并打印结果；然后运行它，告诉我实际输出。
- Case file: `replay_cases_challenge\regression\challenge_script_create_run.json`
- Fresh trace: `traces\20260704T063524-10eef9ec`

### challenge_tool_error_then_shell

- Status: `PASS`
- Score: `100`
- Category: `shell`
- Tags: `regression, shell, uses_tools, tool:exec, tool:list_dir`
- Purpose: Protect shell behavior for: 先尝试运行 challenge/no_such_script.py；如果失败，再查看 challenge 目录内容，并说明失败原因和可执行的修复步骤。
- Case file: `replay_cases_challenge\regression\challenge_tool_error_then_shell.json`
- Fresh trace: `traces\20260704T063532-126b942c`

### challenge_workspace_summary_from_tools

- Status: `FAIL`
- Score: `88`
- Category: `filesystem`
- Tags: `regression, filesystem, uses_tools, tool:list_dir, tool:read_file, read_only`
- Purpose: Protect filesystem behavior for: 不要凭记忆回答。请查看当前 workspace 根目录和 test 目录，然后总结有哪些可用的测试或演示文件。
- Case file: `replay_cases_challenge\regression\challenge_workspace_summary_from_tools.json`
- Fresh trace: `traces\20260704T063546-ced0a554`
