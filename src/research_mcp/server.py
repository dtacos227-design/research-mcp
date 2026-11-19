from mcp.server.mcpserver import MCPServer

from research_mcp.config import configure_logging, get_settings

mcp = MCPServer("research-mcp")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mcp.run()


if __name__ == "__main__":
    main()
