from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from minisweagent.run.mcp import (
    MCPServerConfig,
    _parse_screen_bounds,
    _pointer_click_command,
    _pointer_move_command,
    _save_medium_jpeg,
    _screenshot_commands,
    _validate_pointer_coordinates,
    create_server,
)


@pytest.mark.asyncio
async def test_mcp_server_exposes_context_prompt_and_bash(tmp_path: Path):
    server = create_server(MCPServerConfig(tmp_path))

    tools = await server.list_tools()
    assert [tool.name for tool in tools] == [
        "get_coding_prompt",
        "upload_file",
        "get_image",
        "get_host_screen",
        "move_host_pointer",
        "click_host_pointer",
        "bash",
    ]
    assert tools[1].meta == {"openai/fileParams": ["file"]}
    assert tools[1].inputSchema["$defs"]["FileReference"]["required"] == ["download_url", "file_id"]
    assert [prompt.name for prompt in await server.list_prompts()] == ["coding_agent"]
    assert [str(resource.uri) for resource in await server.list_resources()] == ["miniswe://context"]
    prompt = await server.get_prompt("coding_agent", {"task": "Fix the bug"})
    assert prompt.messages[0].content.text.startswith(
        "<system_prompt>\nYou are a helpful assistant that can interact with a computer.\n</system_prompt>"
    )
    assert "Please solve this issue: Fix the bug" in prompt.messages[0].content.text
    result = await server.call_tool("get_coding_prompt", {"task": "Fix the bug"})
    assert result[1]["result"].startswith(
        "<system_prompt>\nYou are a helpful assistant that can interact with a computer.\n</system_prompt>"
    )
    assert "Please solve this issue: Fix the bug" in result[1]["result"]

    result = await server.call_tool("bash", {"command": "printf hello > test.txt && cat test.txt"})
    assert result[1] == {
        "output": "hello",
        "returncode": 0,
        "exception_info": "",
    }
    assert (tmp_path / "test.txt").read_text() == "hello"


@pytest.mark.asyncio
async def test_mcp_upload_file_rejects_unsafe_paths_and_urls(tmp_path: Path):
    server = create_server(MCPServerConfig(tmp_path))
    file = {"download_url": "http://example.com/file", "file_id": "file_123"}

    with pytest.raises(ToolError, match="inside the workspace"):
        await server.call_tool("upload_file", {"destination_path": "../file", "file": file})
    with pytest.raises(ToolError, match="must use HTTPS"):
        await server.call_tool("upload_file", {"destination_path": "file", "file": file})


@pytest.mark.asyncio
async def test_mcp_bash_preserves_completion_signal(tmp_path: Path):
    result = await create_server(MCPServerConfig(tmp_path)).call_tool(
        "bash", {"command": "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\ndone'"}
    )

    assert result[1] == {
        "output": "done",
        "returncode": 0,
        "exception_info": "",
        "completed": True,
    }


def test_mcp_server_requires_directory(tmp_path: Path):
    path = tmp_path / "file"
    path.write_text("")

    with pytest.raises(NotADirectoryError):
        create_server(MCPServerConfig(path))


@pytest.mark.asyncio
async def test_mcp_get_image_returns_image_content(tmp_path: Path):
    image = tmp_path / "preview.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\npreview")
    server = create_server(MCPServerConfig(tmp_path))

    result = await server.call_tool("get_image", {"path": "preview.png"})

    assert len(result) == 1
    assert result[0].type == "image"
    assert result[0].mimeType == "image/png"
    assert result[0].data


@pytest.mark.asyncio
async def test_mcp_get_image_rejects_unsafe_or_invalid_files(tmp_path: Path):
    server = create_server(MCPServerConfig(tmp_path))
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "large.png").write_bytes(b"0" * (10 * 1024 * 1024 + 1))

    with pytest.raises(ToolError, match="inside the workspace"):
        await server.call_tool("get_image", {"path": "../outside.png"})
    with pytest.raises(ToolError, match="does not exist"):
        await server.call_tool("get_image", {"path": "missing.png"})
    with pytest.raises(ToolError, match="Supported formats"):
        await server.call_tool("get_image", {"path": "notes.txt"})
    with pytest.raises(ToolError, match="10 MiB"):
        await server.call_tool("get_image", {"path": "large.png"})


@pytest.mark.asyncio
async def test_mcp_get_host_screen_rejects_unsafe_or_invalid_paths(tmp_path: Path):
    server = create_server(MCPServerConfig(tmp_path))

    with pytest.raises(ToolError, match="inside the workspace"):
        await server.call_tool("get_host_screen", {"path": "../screen.jpg"})
    with pytest.raises(ToolError, match="JPEG"):
        await server.call_tool("get_host_screen", {"path": "screen.png"})


def test_mcp_get_host_screen_supports_macos_and_linux_commands(tmp_path: Path):
    assert _screenshot_commands(tmp_path / "screen.png", "darwin") == [
        ["screencapture", "-x", "-t", "png", str(tmp_path / "screen.png")]
    ]
    assert ["grim", str(tmp_path / "screen.png")] in _screenshot_commands(tmp_path / "screen.png", "linux")
    assert ["grim", str(tmp_path / "screen.jpg")] not in _screenshot_commands(tmp_path / "screen.jpg", "linux")


def test_mcp_save_medium_jpeg_returns_resolution(tmp_path: Path):
    from PIL import Image as PILImage

    source = tmp_path / "screen.png"
    destination = tmp_path / "screen.jpg"
    PILImage.new("RGB", (4, 3), "red").save(source)

    assert _save_medium_jpeg(source, destination) == (4, 3)
    assert destination.read_bytes().startswith(b"\xff\xd8")


def test_mcp_save_medium_jpeg_resizes_to_logical_resolution(tmp_path: Path):
    from PIL import Image as PILImage

    source = tmp_path / "screen.png"
    destination = tmp_path / "screen.jpg"
    PILImage.new("RGB", (8, 6), "red").save(source)

    assert _save_medium_jpeg(source, destination, (4, 3)) == (4, 3)


def test_mcp_parse_screen_bounds():
    assert _parse_screen_bounds("0, 0, 1637, 1024") == (1637, 1024)
    assert _parse_screen_bounds("-100, 0, 1637, 1024") == (1737, 1024)


def test_mcp_pointer_commands_support_macos_and_linux():
    assert _pointer_move_command(10, 20, "darwin") == ["cliclick", "m:10,20"]
    assert _pointer_click_command(10, 20, "left", "darwin") == ["cliclick", "c:10,20"]
    assert _pointer_click_command(10, 20, "right", "darwin") == ["cliclick", "rc:10,20"]
    assert _pointer_move_command(10, 20, "linux") == ["xdotool", "mousemove", "10", "20"]
    assert _pointer_click_command(10, 20, "right", "linux") == [
        "xdotool",
        "mousemove",
        "10",
        "20",
        "click",
        "3",
    ]


def test_mcp_pointer_coordinates_must_be_non_negative():
    _validate_pointer_coordinates(0, 0)
    with pytest.raises(ValueError, match="non-negative"):
        _validate_pointer_coordinates(-1, 0)
    with pytest.raises(ValueError, match="non-negative"):
        _validate_pointer_coordinates(0, -1)
