{% if system == "Windows" %}
## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer `list_dir`, `read_file`, `edit_file`, and `write_file` for filesystem work.
- Use `exec` for tests, builds, package commands, and git commands.
- If terminal output is garbled, retry with UTF-8 friendly command output.
{% else %}
## Platform Policy (POSIX)
- You are running on a POSIX platform. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or safer than shell commands.
{% endif %}
