"""Top-level CLI dispatcher for the AI Prophet ecosystem."""

from __future__ import annotations

import click

from ai_prophet.forecast.main import cli as forecast_cli
from ai_prophet.trade.main import cli as trade_cli


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """AI Prophet ecosystem commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Show root help."""
    parent = ctx.parent or ctx
    click.echo(parent.command.get_help(parent))


cli.add_command(trade_cli)
cli.add_command(forecast_cli)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
