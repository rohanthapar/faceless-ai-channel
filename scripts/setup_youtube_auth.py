#!/usr/bin/env python3
"""
scripts/setup_youtube_auth.py — One-time YouTube OAuth2 setup.

Run this ONCE to authorize the app to upload to your YouTube channel.
A browser window will open — sign in and approve. The token is saved to
credentials/youtube_token.json and reused automatically forever.

Usage:
    python scripts/setup_youtube_auth.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(Panel(
        "[bold yellow]YouTube OAuth Setup[/bold yellow]\n\n"
        "This will open a browser window to authorize the app.\n"
        "Sign in with the Google account that owns your YouTube channel.\n\n"
        "[dim]Token saved to: credentials/youtube_token.json[/dim]",
        border_style="yellow",
    ))

    from app.config import settings
    if not settings.youtube_client_secret_file.exists():
        console.print(f"\n[red]❌ Missing:[/red] {settings.youtube_client_secret_file}\n")
        console.print("[bold]Steps to get client_secret.json:[/bold]")
        console.print("  1. Go to [link]https://console.cloud.google.com[/link]")
        console.print("  2. Create a project (or select existing)")
        console.print("  3. Enable [bold]YouTube Data API v3[/bold]")
        console.print("     APIs & Services → Enable APIs → search 'YouTube Data API v3'")
        console.print("  4. Create OAuth credentials:")
        console.print("     APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID")
        console.print("     Application type: [bold]Desktop app[/bold]")
        console.print("     Download JSON → rename to [bold]youtube_client_secret.json[/bold]")
        console.print(f"  5. Place it at: [bold]{settings.youtube_client_secret_file}[/bold]\n")
        console.print("Then re-run this script.")
        return False

    console.print("\n[dim]Opening browser for authorization...[/dim]\n")

    from app.publishing.youtube import run_oauth_flow
    ok = await run_oauth_flow()

    if ok:
        console.print("\n[bold green]✅ YouTube authorization complete![/bold green]")
        console.print(f"Token saved to: {settings.youtube_token_file}")
        console.print("\nNext: set [bold]YOUTUBE_PRODUCTION_MODE=true[/bold] in .env to enable uploads.")
    else:
        console.print("\n[red]❌ Authorization failed — check the error above.[/red]")

    return ok


if __name__ == "__main__":
    asyncio.run(main())
