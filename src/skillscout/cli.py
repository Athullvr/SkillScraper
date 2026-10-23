"""Typer command-line interface for SkillScout."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from skillscout import __version__
from skillscout.application import doctor_report, inspect_skill, prepare_recommendation
from skillscout.catalog import (
    CatalogAuthenticationError,
    CatalogClient,
    CatalogDataError,
    CatalogError,
    CatalogCredentialsMissingError,
    CatalogCredentialsRejectedError,
    CatalogHTTPError,
    CatalogNotFoundError,
    CatalogRateLimitError,
    CatalogServiceError,
    CatalogTimeoutError,
    CatalogUnavailableError,
)
from skillscout.config import ConfigurationError, load_config
from skillscout.llm import LLMAdapter
from skillscout.models import Decision, ErrorDetail, ErrorReport
from skillscout.project_context import ProjectContextError, read_project_context
from skillscout.report import (
    escape,
    json_report,
    render_doctor,
    render_inspection,
    render_recommendation,
)

app = typer.Typer(
    name="skillscout",
    help="Find and inspect agent skills without installing or activating them.",
    no_args_is_help=True,
    add_completion=False,
)
ERROR_CONSOLE = Console(stderr=True, highlight=False)
VALID_FORMATS = Literal["text", "json"]


@app.command()
def recommend(
    task: str = typer.Argument(..., help="Coding task to assess."),
    project: Path = typer.Option(
        Path("."), "--project", help="Project directory for bounded metadata detection."
    ),
    agent: str = typer.Option(
        "codex", "--agent", help="Target agent for the inert install command."
    ),
    output_format: VALID_FORMATS = typer.Option(
        "text", "--format", help="Output format: terminal text or versioned JSON."
    ),
) -> None:
    """Assess whether a skill adds value, then inspect a bounded set of candidates."""
    try:
        report = prepare_recommendation(
            task,
            project,
            agent,
            config_loader=load_config,
            project_context_reader=read_project_context,
            catalog_client_factory=CatalogClient,
            llm_adapter_factory=LLMAdapter,
        )
        _emit(report, output_format)
        if report.decision == Decision.AVOID:
            raise typer.Exit(code=4)
    except typer.Exit:
        raise
    except ConfigurationError as exc:
        _emit_error(str(exc), output_format, code="configuration_error", exit_code=2)
    except (ProjectContextError, ValueError) as exc:
        _emit_error(str(exc), output_format, code="input_error", exit_code=2)
    except CatalogCredentialsMissingError as exc:
        _emit_error(str(exc), output_format, code="catalog_credentials_missing", exit_code=3)
    except CatalogCredentialsRejectedError as exc:
        _emit_error(str(exc), output_format, code="catalog_credentials_rejected", exit_code=3)
    except CatalogAuthenticationError as exc:
        _emit_error(str(exc), output_format, code="catalog_authentication_error", exit_code=3)
    except CatalogTimeoutError as exc:
        _emit_error(str(exc), output_format, code="catalog_timeout", exit_code=3)
    except CatalogRateLimitError as exc:
        _emit_error(str(exc), output_format, code="catalog_rate_limited", exit_code=3)
    except CatalogServiceError as exc:
        _emit_error(str(exc), output_format, code="catalog_service_error", exit_code=3)
    except CatalogNotFoundError as exc:
        _emit_error(str(exc), output_format, code="catalog_not_found", exit_code=3)
    except CatalogHTTPError as exc:
        _emit_error(str(exc), output_format, code="catalog_http_error", exit_code=3)
    except CatalogUnavailableError as exc:
        _emit_error(str(exc), output_format, code="catalog_unavailable", exit_code=3)
    except CatalogDataError as exc:
        _emit_error(str(exc), output_format, code="catalog_data_error", exit_code=3)
    except CatalogError as exc:
        _emit_error(str(exc), output_format, code="catalog_error", exit_code=3)


@app.command()
def inspect(
    skill_id: str = typer.Argument(
        ..., help="Stable skills.sh skill ID, such as owner/repo/skill."
    ),
    project: Path = typer.Option(
        Path("."), "--project", help="Project directory for bounded metadata detection."
    ),
    output_format: VALID_FORMATS = typer.Option(
        "text", "--format", help="Output format: text or JSON."
    ),
) -> None:
    """Fetch and inspect a skill revision without installing it."""
    try:
        report = inspect_skill(
            skill_id,
            project,
            config_loader=load_config,
            project_context_reader=read_project_context,
            catalog_client_factory=CatalogClient,
        )
        if output_format == "json":
            typer.echo(json_report(report))
        else:
            render_inspection(report)
        if report.decision == Decision.AVOID:
            raise typer.Exit(code=4)
    except typer.Exit:
        raise
    except ConfigurationError as exc:
        _emit_error(str(exc), output_format, code="configuration_error", exit_code=2)
    except (ProjectContextError, ValueError) as exc:
        _emit_error(str(exc), output_format, code="input_error", exit_code=2)
    except CatalogCredentialsMissingError as exc:
        _emit_error(str(exc), output_format, code="catalog_credentials_missing", exit_code=3)
    except CatalogCredentialsRejectedError as exc:
        _emit_error(str(exc), output_format, code="catalog_credentials_rejected", exit_code=3)
    except CatalogAuthenticationError as exc:
        _emit_error(str(exc), output_format, code="catalog_authentication_error", exit_code=3)
    except CatalogTimeoutError as exc:
        _emit_error(str(exc), output_format, code="catalog_timeout", exit_code=3)
    except CatalogRateLimitError as exc:
        _emit_error(str(exc), output_format, code="catalog_rate_limited", exit_code=3)
    except CatalogServiceError as exc:
        _emit_error(str(exc), output_format, code="catalog_service_error", exit_code=3)
    except CatalogNotFoundError as exc:
        _emit_error(str(exc), output_format, code="catalog_not_found", exit_code=3)
    except CatalogHTTPError as exc:
        _emit_error(str(exc), output_format, code="catalog_http_error", exit_code=3)
    except CatalogUnavailableError as exc:
        _emit_error(str(exc), output_format, code="catalog_unavailable", exit_code=3)
    except CatalogDataError as exc:
        _emit_error(str(exc), output_format, code="catalog_data_error", exit_code=3)
    except CatalogError as exc:
        _emit_error(str(exc), output_format, code="catalog_error", exit_code=3)


@app.command()
def doctor() -> None:
    """Check local runtime and configuration without making network requests."""
    try:
        report = doctor_report(config_loader=load_config)
    except ConfigurationError as exc:
        _emit_error(str(exc), "text", code="configuration_error", exit_code=2)
        return
    render_doctor(report)
    if not report.python_supported:
        raise typer.Exit(code=2)


@app.command(name="mcp")
def mcp_command() -> None:
    """Start the local stdio MCP server for compatible coding agents."""
    from skillscout.mcp_entrypoint import main as mcp_main

    mcp_main()


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", help="Show the SkillScout version and exit."),
) -> None:
    if version:
        typer.echo(f"SkillScout {__version__}")
        raise typer.Exit()


def _emit(report, output_format: str) -> None:
    if output_format == "json":
        typer.echo(json_report(report))
    else:
        render_recommendation(report)


def _emit_error(message: str, output_format: str, *, code: str, exit_code: int) -> None:
    if output_format == "json":
        typer.echo(json_report(ErrorReport(error=ErrorDetail(code=code, message=message))))
    else:
        ERROR_CONSOLE.print(f"[bold red]Error:[/bold red] {escape(message)}")
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
