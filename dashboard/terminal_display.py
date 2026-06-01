"""Live terminal dashboard for Polymarket market scanner using rich."""

from datetime import datetime, timezone
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box
from loguru import logger

from data.database import get_market_count, get_crypto_market_count
from intelligence.market_scorer import get_top_markets


def _days_until(end_date_str: str) -> str:
    """Calculate days until a given end date."""
    if not end_date_str:
        return "N/A"
    try:
        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = (end_date - now).days
        if days < 0:
            return "0"
        return str(days)
    except (ValueError, TypeError):
        return "N/A"


def _format_volume(vol: float) -> str:
    """Format volume as $X,XXX.XX."""
    if vol >= 1_000_000:
        return f"${vol/1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"${vol:,.0f}"
    else:
        return f"${vol:.2f}"


def _truncate(text: str, max_len: int = 50) -> str:
    """Truncate text to max_len with ellipsis."""
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def build_table(last_fetch: str = "Never") -> Table:
    """Build the rich Table with top 20 crypto markets."""
    table = Table(
        title="",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold bright_cyan",
        title_style="bold white",
    )

    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Market", width=52, no_wrap=True)
    table.add_column("YES", width=7, justify="right")
    table.add_column("NO", width=7, justify="right")
    table.add_column("Volume", width=10, justify="right")
    table.add_column("Score", width=7, justify="right")
    table.add_column("Days", width=5, justify="center")

    markets = get_top_markets(20)

    for i, m in enumerate(markets, 1):
        yes_price = float(m.get("yes_price", 0.5))
        no_price = float(m.get("no_price", 0.5))
        volume = float(m.get("volume", 0))
        score = float(m.get("score", 0))

        # Color YES price green if >0.5, red if <0.5
        yes_str = f"{yes_price:.2f}"
        if yes_price > 0.5:
            yes_str = f"[green]{yes_price:.2f}[/green]"
        elif yes_price < 0.5:
            yes_str = f"[red]{yes_price:.2f}[/red]"

        no_str = f"{no_price:.2f}"

        table.add_row(
            str(i),
            _truncate(m.get("question", "Unknown"), 50),
            yes_str,
            no_str,
            _format_volume(volume),
            f"{score:.2f}",
            _days_until(m.get("end_date", "")),
        )

    return table


def build_header(last_fetch: str = "Never") -> Panel:
    """Build the header panel."""
    total = get_market_count()
    crypto = get_crypto_market_count()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = Text()
    content.append("POLYMARKET BOT — MARKET SCANNER\n", style="bold bright_yellow")
    content.append(f"Timestamp: ", style="bold white")
    content.append(f"{now}\n", style="cyan")
    content.append(f"Total Markets: ", style="bold white")
    content.append(f"{total:,}  ", style="green")
    content.append(f"|  Crypto Markets: ", style="bold white")
    content.append(f"{crypto:,}\n", style="green")
    content.append(f"Last Fetch: ", style="bold white")
    content.append(f"{last_fetch}", style="yellow")

    return Panel(content, box=box.DOUBLE_EDGE, border_style="bright_yellow")


def run_dashboard(last_fetch: str = "Never"):
    """Run the live terminal dashboard that refreshes every 30 seconds."""
    logger.info("Starting live terminal dashboard")

    with Live(refresh_per_second=2, screen=True) as live:
        try:
            while True:
                header = build_header(last_fetch)
                table = build_table(last_fetch)
                layout = Layout()
                layout.split_column(
                    Layout(header, size=7),
                    Layout(table),
                )
                live.update(layout)
                # Use a manual loop — Live handles refresh, we just update data
                from time import sleep
                sleep(30)
        except KeyboardInterrupt:
            logger.info("Dashboard stopped by user")