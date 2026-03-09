"""Forecast CLI namespace for the Prophet Arena benchmark."""

from __future__ import annotations

import click


@click.group(name="forecast", invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Forecast ecosystem commands."""
    if ctx.invoked_subcommand is None:
        click.echo("Forecast commands are not implemented yet.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
