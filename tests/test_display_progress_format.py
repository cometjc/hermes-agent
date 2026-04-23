from __future__ import annotations

import json
from pathlib import Path


def test_cute_tool_message_uses_relative_paths(tmp_path, monkeypatch):
    from agent.display import get_cute_tool_message

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "example.py"
    msg = get_cute_tool_message("patch", {"path": str(target)}, 1.2)

    assert "nested/example.py" in msg
    assert str(target) not in msg


def test_render_edit_diff_with_delta_uses_patch_fence(tmp_path, monkeypatch):
    from agent.display import capture_local_edit_snapshot, render_edit_diff_with_delta

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "gateway" / "run.py"
    path.parent.mkdir(parents=True)
    path.write_text("line 1\nline 2\n", encoding="utf-8")

    snapshot = capture_local_edit_snapshot("write_file", {"path": str(path)})
    path.write_text("line 1\nline 2 changed\n", encoding="utf-8")

    lines: list[str] = []
    ok = render_edit_diff_with_delta(
        "write_file",
        json.dumps({"success": True}),
        function_args={"path": str(path)},
        snapshot=snapshot,
        print_fn=lines.append,
    )

    assert ok is True
    assert lines[0].startswith("✍️ write_file gateway/run.py")
    assert lines[1] == "```patch"
    assert lines[-1] == "```"
    assert any("-" in line for line in lines)
    assert any("+" in line for line in lines)
