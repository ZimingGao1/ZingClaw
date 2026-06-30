from __future__ import annotations

from mcp.server.fastmcp import FastMCP

app = FastMCP("demo")


@app.tool(name="echo", description="Echo a value back.")
def echo(value: str) -> str:
    return f"echo:{value}"


@app.tool(name="shout", description="Uppercase a value.")
def shout(value: str) -> str:
    return value.upper()


@app.resource("memo://greeting", name="greeting", description="A demo greeting resource.")
def greeting() -> str:
    return "hello from resource"


@app.prompt(name="review_plan", description="Return a short review prompt.")
def review_plan(topic: str) -> str:
    return f"Review topic: {topic}"


if __name__ == "__main__":
    app.run("stdio")
