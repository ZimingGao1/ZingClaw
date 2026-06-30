from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


def build_app(*, host: str, port: int) -> FastMCP:
    app = FastMCP("demo-http", host=host, port=port)

    @app.tool(name="echo", description="Echo a value back.")
    def echo(value: str) -> str:
        return f"echo:{value}"

    @app.resource("memo://greeting", name="greeting", description="A demo greeting resource.")
    def greeting() -> str:
        return "hello from http resource"

    @app.prompt(name="review_plan", description="Return a short review prompt.")
    def review_plan(topic: str) -> str:
        return f"Review topic: {topic}"

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="streamable-http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    app = build_app(host=args.host, port=args.port)
    app.run(args.transport)
