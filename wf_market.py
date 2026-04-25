#!/usr/bin/env python3
"""
Warframe farming guide — tells you what to aim for in gameplay
based on the current 48h market economy (price × demand).
"""

import asyncio
import argparse
import sys
from curl_cffi.requests import AsyncSession
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.columns import Columns
from rich import box

BASE_V2 = "https://api.warframe.market/v2"
BASE_V1 = "https://api.warframe.market/v1"
HEADERS = {"Accept": "application/json", "platform": "pc", "language": "en"}

console = Console()

# Category priority and display labels (order matters for classification)
CATEGORIES = [
    ("arcane",     "Arcane"),
    ("riven",      "Riven"),
    ("mod",        "Mod"),
    ("prime",      "Prime"),
    ("syndicate",  "Syndicate"),
    ("warframe",   "Warframe"),
    ("weapon",     "Weapon"),
    ("companion",  "Companion"),
    ("sentinel",   "Sentinel"),
    ("resource",   "Resource"),
    ("key",        "Key"),
]

CATEGORY_COLORS = {
    "Arcane":    "magenta",
    "Riven":     "bright_red",
    "Mod":       "bright_yellow",
    "Prime":     "gold1",
    "Syndicate": "bright_cyan",
    "Warframe":  "bright_blue",
    "Weapon":    "bright_green",
    "Companion": "green",
    "Sentinel":  "cyan",
    "Resource":  "white",
    "Key":       "dim white",
    "Other":     "dim white",
}

DEMAND_TIERS = [
    (100, "[bold green]Very High[/bold green]"),
    (40,  "[green]High[/green]"),
    (15,  "[yellow]Medium[/yellow]"),
    (5,   "[dim]Low[/dim]"),
    (0,   "[dim red]Very Low[/dim red]"),
]


# ── helpers ────────────────────────────────────────────────────────────────────

def get_name(item: dict) -> str:
    return item["i18n"]["en"]["name"]


def get_category(tags: list[str]) -> str:
    for tag, label in CATEGORIES:
        if tag in tags:
            return label
    return "Other"


def demand_label(volume: int) -> str:
    for threshold, label in DEMAND_TIERS:
        if volume >= threshold:
            return label
    return DEMAND_TIERS[-1][1]


def format_flow(flow: float) -> str:
    if flow >= 10_000:
        return f"{flow/1000:.1f}k"
    return str(int(flow))


# ── networking ─────────────────────────────────────────────────────────────────

async def _get(session: AsyncSession, url: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            r = await session.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return None


async def fetch_all_items(session: AsyncSession) -> list[dict]:
    try:
        r = await session.get(f"{BASE_V2}/items", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()["data"]
        console.print(f"[red]HTTP {r.status_code}:[/red] {r.text[:200]}")
    except Exception as e:
        console.print(f"[red]Connection error:[/red] {type(e).__name__}: {e}")
    console.print("[red]Could not reach warframe.market — check your connection.[/red]")
    sys.exit(1)


async def fetch_stats(
    session: AsyncSession, slug: str, sem: asyncio.Semaphore
) -> dict | None:
    data = await _get(session, f"{BASE_V1}/items/{slug}/statistics", sem)
    if not data:
        return None
    try:
        closed = data["payload"]["statistics_closed"]["48hours"]
        if not closed:
            return None
        volume   = sum(s.get("volume", 0) for s in closed)
        if volume == 0:
            return None
        prices   = [s for s in closed if s.get("min_price") and s.get("max_price")]
        if not prices:
            return None
        min_p    = min(s["min_price"] for s in prices)
        max_p    = max(s["max_price"] for s in prices)
        wa_total = sum(s.get("wa_price", 0) * s.get("volume", 0) for s in closed)
        avg_p    = round(wa_total / volume, 1)
        return {
            "volume":    volume,
            "avg_price": avg_p,
            "min_price": min_p,
            "max_price": max_p,
            "flow":      round(avg_p * volume, 1),  # total plat flowing through this item in 48h
        }
    except (KeyError, ValueError, ZeroDivisionError):
        return None


# ── display ────────────────────────────────────────────────────────────────────

def build_table(rows: list[dict], title: str, start_rank: int = 1) -> Table:
    table = Table(title=title, box=box.ROUNDED, show_lines=False, highlight=True, title_style="bold")
    table.add_column("#",          style="dim",   width=3,  justify="right")
    table.add_column("Item",       min_width=26)
    table.add_column("Category",   width=11)
    table.add_column("Avg (p)",    width=8,  justify="right")
    table.add_column("Range",      width=12, justify="center")
    table.add_column("Demand",     width=11, justify="center")
    table.add_column("48h Flow",   width=9,  justify="right", style="bold yellow")

    for rank, row in enumerate(rows, start_rank):
        cat   = row["category"]
        color = CATEGORY_COLORS.get(cat, "white")
        avg   = row["avg_price"]
        avg_style = "bold green" if avg >= 100 else "green" if avg >= 30 else "yellow" if avg >= 10 else "dim"

        table.add_row(
            str(rank),
            f"[{color}]{row['name']}[/{color}]",
            f"[{color}]{cat}[/{color}]",
            f"[{avg_style}]{avg}p[/{avg_style}]",
            f"[dim]{row['min_price']}–{row['max_price']}p[/dim]",
            Text.from_markup(demand_label(row["volume"])),
            format_flow(row["flow"]),
        )
    return table


# ── main ───────────────────────────────────────────────────────────────────────

async def run(filter_tag: str | None, top_n: int, group: bool) -> None:
    console.print(Rule("[bold cyan]Warframe — What to Farm Right Now[/bold cyan]"))
    console.print("[dim]Based on 48h market data  ·  Sorted by demand × price[/dim]\n")

    sem = asyncio.Semaphore(20)

    async with AsyncSession(impersonate="chrome120") as session:

        # 1. Item list
        with console.status("[yellow]Fetching item list...[/yellow]"):
            items = await fetch_all_items(session)

        # 2. Filter
        if filter_tag:
            kw = filter_tag.lower()
            filtered = [i for i in items if kw in i.get("tags", [])]
            if not filtered:
                # fallback: name substring
                filtered = [i for i in items if kw in get_name(i).lower()]
            if not filtered:
                console.print(f"[red]No items found for '[bold]{filter_tag}[/bold]'.[/red]")
                console.print("[dim]Common tags: prime, arcane, mod, riven, warframe, weapon, syndicate, key[/dim]")
                sys.exit(1)
            console.print(f"[dim]Checking {len(filtered)} '{filter_tag}' items...[/dim]")
        else:
            filtered = items
            console.print(f"[dim]Checking {len(filtered)} items...[/dim]")

        # 3. Fetch statistics concurrently
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"),
            BarColumn(), TaskProgressColumn(),
            console=console, transient=True,
        ) as prog:
            tid = prog.add_task("[yellow]Fetching 48h stats...[/yellow]", total=len(filtered))

            async def _stats(item: dict) -> dict | None:
                res = await fetch_stats(session, item["slug"], sem)
                prog.advance(tid)
                return res

            results = await asyncio.gather(*[_stats(i) for i in filtered])

    # 4. Build ranked list
    combined: list[dict] = []
    for item, stats in zip(filtered, results):
        if stats:
            combined.append({
                "name":     get_name(item),
                "category": get_category(item.get("tags", [])),
                "slug":     item["slug"],
                **stats,
            })

    combined.sort(key=lambda x: x["flow"], reverse=True)

    if not combined:
        console.print("[red]No results found — try a different filter.[/red]")
        return

    # 5. Display
    if group and not filter_tag:
        # Group by category, show top-n per group
        from collections import defaultdict
        by_cat: dict[str, list] = defaultdict(list)
        for row in combined:
            by_cat[row["category"]].append(row)

        # Sort categories by their top item's flow
        cat_order = sorted(by_cat.keys(), key=lambda c: by_cat[c][0]["flow"], reverse=True)

        console.print(f"[dim]Showing top {top_n} per category, ranked by 48h flow[/dim]\n")
        for cat in cat_order:
            rows = by_cat[cat][:top_n]
            color = CATEGORY_COLORS.get(cat, "white")
            table = build_table(rows, f"[{color}]{cat}[/{color}]")
            console.print(table)
            console.print()
    else:
        # Flat list
        top = combined[:top_n]
        title = f"Top {len(top)} Items to Farm Now"
        if filter_tag:
            title += f"  ·  [{CATEGORY_COLORS.get(get_category([filter_tag]), 'white')}]{filter_tag.capitalize()}[/]"
        console.print(build_table(top, title))

    console.print(
        "\n[dim]"
        "Avg (p) = weighted avg sell price  ·  "
        "Range = 48h low–high  ·  "
        "48h Flow = avg × volume (total plat flowing through item)[/dim]"
    )
    console.print(
        "[dim]"
        "Demand: [bold green]Very High[/bold green]≥100  "
        "[green]High[/green]≥40  "
        "[yellow]Medium[/yellow]≥15  "
        "[dim]Low[/dim]≥5  "
        "[dim red]Very Low[/dim red]<5"
        "[/dim]"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="See what items are worth farming right now based on live market data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python wf_market.py                      # Top 20 overall
  python wf_market.py --group              # Top 5 per category
  python wf_market.py --type prime         # Prime parts & sets
  python wf_market.py --type arcane -n 15  # Top 15 arcanes
  python wf_market.py --type mod
  python wf_market.py --type syndicate
  python wf_market.py --type riven
        """,
    )
    parser.add_argument(
        "--type", "-t",
        dest="filter_tag", metavar="TAG",
        help="Filter by tag: prime, arcane, mod, riven, warframe, weapon, syndicate, key...",
    )
    parser.add_argument(
        "--top", "-n",
        type=int, default=20, metavar="N",
        help="Results to show (default: 20; per category when --group is used)",
    )
    parser.add_argument(
        "--group", "-g",
        action="store_true",
        help="Group results by category (arcanes, mods, primes, etc.)",
    )
    args = parser.parse_args()
    asyncio.run(run(filter_tag=args.filter_tag, top_n=args.top, group=args.group))
