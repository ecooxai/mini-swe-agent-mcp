#!/usr/bin/env python3

"""Serve mini-SWE-agent's coding environment over MCP."""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any, Literal

import requests
import typer
import yaml
from jinja2 import StrictUndefined, Template
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image
from pydantic import BaseModel

from minisweagent.config import builtin_config_dir
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted


@dataclass
class MCPServerConfig:
    workspace: Path
    timeout: int = 30
    host: str = "127.0.0.1"
    port: int = 8000


class FileReference(BaseModel):
    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


def _get_image(workspace: Path, path: str | Path) -> Image:
    image_path = (workspace / path).resolve()
    if not image_path.is_relative_to(workspace):
        raise ValueError("path must be inside the workspace")
    if not image_path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        raise ValueError("Supported formats: PNG, JPEG, GIF, and WebP")
    if image_path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Image exceeds the 10 MiB limit")
    return Image(path=image_path)


def _screenshot_commands(path: Path, platform: str = sys.platform) -> list[list[str]]:
    if platform == "darwin":
        return [["screencapture", "-x", "-t", path.suffix.lower().lstrip(".").replace("jpeg", "jpg"), str(path)]]
    if platform.startswith("linux"):
        commands = [
            ["gnome-screenshot", "-f", str(path)],
            ["scrot", str(path)],
            ["maim", str(path)],
            ["import", "-window", "root", str(path)],
        ]
        if path.suffix.lower() == ".png":
            commands.extend([["grim", str(path)], ["spectacle", "-b", "-n", "-o", str(path)]])
        return commands
    return []


def _capture_screen(path: Path) -> None:
    commands = [command for command in _screenshot_commands(path) if which(command[0])]
    if not commands:
        raise RuntimeError(
            "No screenshot command found. On Linux, install gnome-screenshot, grim, spectacle, scrot, maim, or ImageMagick."
        )
    for command in commands:
        try:
            subprocess.run(command, check=True)
            return
        except subprocess.CalledProcessError:
            pass
    raise RuntimeError("All screenshot commands failed")


def _pointer_move_command(x: int, y: int, platform: str = sys.platform) -> list[str]:
    if platform == "darwin":
        return ["cliclick", f"m:{x},{y}"]
    if platform.startswith("linux"):
        return ["xdotool", "mousemove", str(x), str(y)]
    return []


def _pointer_click_command(x: int, y: int, button: str, platform: str = sys.platform) -> list[str]:
    if platform == "darwin":
        return ["cliclick", f"{'rc' if button == 'right' else 'c'}:{x},{y}"]
    if platform.startswith("linux"):
        return ["xdotool", "mousemove", str(x), str(y), "click", "3" if button == "right" else "1"]
    return []


def _run_pointer_command(command: list[str]) -> None:
    if not command or not which(command[0]):
        raise RuntimeError("No pointer command found. On macOS, install cliclick. On Linux, install xdotool.")
    subprocess.run(command, check=True)


def _validate_pointer_coordinates(x: int, y: int) -> None:
    if x < 0 or y < 0:
        raise ValueError("x and y must be non-negative screen coordinates")


def _save_medium_jpeg(source: Path, destination: Path) -> tuple[int, int]:
    from PIL import Image as PILImage

    with PILImage.open(source) as image:
        image.convert("RGB").save(destination, "JPEG", quality=60, optimize=True)
        return image.size


def _get_instructions(env: LocalEnvironment, task: str) -> str:
    template = yaml.safe_load((builtin_config_dir / "mini.yaml").read_text())["agent"]
    variables = env.get_template_vars(task=task)
    system_prompt = Template(template["system_template"], undefined=StrictUndefined).render(**variables)
    instance_prompt = Template(template["instance_template"], undefined=StrictUndefined).render(**variables)
    return f"<system_prompt>\n{system_prompt}\n</system_prompt>\n\n{instance_prompt}"


def create_server(config: MCPServerConfig) -> FastMCP:
    workspace = config.workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    env = LocalEnvironment(
        cwd=str(workspace),
        timeout=config.timeout,
        env={"PAGER": "cat", "MANPAGER": "cat", "LESS": "-R", "PIP_PROGRESS_BAR": "off", "TQDM_DISABLE": "1"},
    )
    instructions = _get_instructions(env, "Follow the user's request.")
    server = FastMCP(
        "mini-SWE-agent",
        instructions=instructions,
        host=config.host,
        port=config.port,
        stateless_http=True,
        json_response=True,
    )

    @server.resource("miniswe://context")
    def context() -> str:
        """Get mini-SWE-agent's coding instructions and workspace context."""
        return instructions

    @server.prompt()
    def coding_agent(task: str) -> str:
        """Get mini-SWE-agent's system prompt and coding workflow for a task."""
        return _get_instructions(env, task)

    @server.tool()
    def get_coding_prompt(task: str) -> str:
        """Use this when you need mini-SWE-agent's system prompt and coding workflow for a task."""
        return _get_instructions(env, task)

    @server.tool(meta={"openai/fileParams": ["file"]})
    def upload_file(destination_path: str, file: FileReference) -> dict[str, Any]:
        """Save a user-attached file to a relative path inside the workspace."""
        destination = (workspace / destination_path).resolve()
        if not destination.is_relative_to(workspace):
            raise ValueError("destination_path must be inside the workspace")
        if not file.download_url.startswith("https://"):
            raise ValueError("file download URL must use HTTPS")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(file.download_url, stream=True, timeout=config.timeout, allow_redirects=False) as response:
            response.raise_for_status()
            with destination.open("wb") as target:
                for chunk in response.iter_content(1024 * 1024):
                    target.write(chunk)
        return {
            "path": str(destination.relative_to(workspace)),
            "size": destination.stat().st_size,
            "file_id": file.file_id,
            "mime_type": file.mime_type,
        }

    @server.tool()
    def get_image(path: str) -> Image:
        """Return a workspace image for inline display in the MCP client."""
        return _get_image(workspace, path)

    @server.tool()
    def get_host_screen(path: str = ".miniswe-agent/host-screen.jpg") -> Any:
        """Capture the host screen on macOS or Linux and return screenshot info plus an inline JPEG image."""
        image_path = (workspace / path).resolve()
        if not image_path.is_relative_to(workspace):
            raise ValueError("path must be inside the workspace")
        if image_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise ValueError("Screenshots must be saved as JPEG")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path = image_path.with_suffix(".capture.png")
        _capture_screen(capture_path)
        width, height = _save_medium_jpeg(capture_path, image_path)
        capture_path.unlink()
        return (
            "\n".join(
                [
                    f"path: {image_path.relative_to(workspace)}",
                    "format: image/jpeg",
                    "quality: 60",
                    f"resolution: {width}x{height}",
                    f"size: {image_path.stat().st_size} bytes",
                ]
            ),
            _get_image(workspace, image_path.relative_to(workspace)),
        )

    @server.tool()
    def move_host_pointer(x: int, y: int) -> dict[str, Any]:
        """Move the host mouse pointer to absolute screen coordinates."""
        _validate_pointer_coordinates(x, y)
        command = _pointer_move_command(x, y)
        _run_pointer_command(command)
        return {"x": x, "y": y, "backend": command[0]}

    @server.tool()
    def click_host_pointer(x: int, y: int, button: Literal["left", "right"] = "left") -> dict[str, Any]:
        """Click the host mouse pointer at absolute screen coordinates with the left or right button."""
        _validate_pointer_coordinates(x, y)
        command = _pointer_click_command(x, y, button)
        _run_pointer_command(command)
        return {"x": x, "y": y, "button": button, "backend": command[0]}

    @server.tool()
    def bash(command: str) -> dict[str, Any]:
        """Execute a bash command in the workspace. Commands may inspect, edit, and test files."""
        try:
            return env.execute({"command": command})
        except Submitted as e:
            return {
                "output": e.messages[0]["extra"]["submission"],
                "returncode": 0,
                "exception_info": "",
                "completed": True,
            }

    return server


app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    workspace: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False, resolve_path=True),
    transport: Literal["stdio", "streamable-http"] = typer.Option("stdio", help="MCP transport to use."),
    host: str = typer.Option("127.0.0.1", help="HTTP bind host."),
    port: int = typer.Option(8000, help="HTTP bind port."),
    timeout: int = typer.Option(30, help="Command timeout in seconds."),
) -> None:
    """Expose WORKSPACE context and command execution to an MCP client."""
    create_server(MCPServerConfig(workspace, timeout, host, port)).run(transport=transport)


if __name__ == "__main__":
    app()
