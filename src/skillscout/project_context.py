"""Read a small allowlist of project metadata without executing project code."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from skillscout.models import ProjectSnapshot

METADATA_FILES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
)
LOCKFILES = (
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "requirements.txt",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "Cargo.lock",
    "go.sum",
)
FRAMEWORK_CONFIGS = (
    "manage.py",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "angular.json",
    "nuxt.config.ts",
    "svelte.config.js",
    "astro.config.mjs",
)
MAX_METADATA_BYTES = 64 * 1024

PYTHON_FRAMEWORKS = {
    "django": "Django",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "starlette": "Starlette",
    "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
}
JS_FRAMEWORKS = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "@angular/core": "Angular",
    "svelte": "Svelte",
    "astro": "Astro",
    "express": "Express",
    "nestjs": "NestJS",
}


class ProjectContextError(ValueError):
    """The requested project location is invalid or cannot be read safely."""


def read_project_context(project_path: str | Path) -> ProjectSnapshot:
    """Read only allowlisted, bounded metadata files directly under the project root."""
    root = Path(project_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise ProjectContextError(f"project path is not a directory: {root}")
    root = root.resolve()

    detected_files: list[str] = []
    facts: set[str] = set()
    inferred: set[str] = set()
    unknowns = ["Source files and project scripts were not read or executed."]
    metadata: dict[str, bytes] = {}

    for filename in dict.fromkeys((*METADATA_FILES, *LOCKFILES, *FRAMEWORK_CONFIGS)):
        candidate = root / filename
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
        except OSError:
            continue
        detected_files.append(filename)
        if filename in METADATA_FILES:
            try:
                with candidate.open("rb") as handle:
                    content = handle.read(MAX_METADATA_BYTES + 1)
                if len(content) > MAX_METADATA_BYTES:
                    unknowns.append(f"{filename} exceeded the {MAX_METADATA_BYTES}-byte read limit.")
                    continue
                metadata[filename] = content
            except OSError:
                unknowns.append(f"{filename} could not be read.")

    pyproject = metadata.get("pyproject.toml")
    if pyproject:
        try:
            parsed = tomllib.loads(pyproject.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            unknowns.append("pyproject.toml was present but could not be parsed.")
        else:
            project = parsed.get("project", {})
            if isinstance(project, dict):
                requires_python = project.get("requires-python")
                if isinstance(requires_python, str):
                    facts.add(f"Python requirement: {requires_python}")
                raw_dependencies = project.get("dependencies", [])
                dependencies = raw_dependencies if isinstance(raw_dependencies, list) else []
                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    dependencies.extend(
                        dep for group in optional.values() if isinstance(group, list) for dep in group
                    )
                _add_dependency_facts(dependencies, PYTHON_FRAMEWORKS, facts)
            poetry = parsed.get("tool", {}).get("poetry", {}) if isinstance(parsed.get("tool", {}), dict) else {}
            if isinstance(poetry, dict):
                poetry_dependencies = poetry.get("dependencies", {})
                if isinstance(poetry_dependencies, dict):
                    _add_dependency_facts(list(poetry_dependencies), PYTHON_FRAMEWORKS, facts)
            tool = parsed.get("tool", {})
            if isinstance(tool, dict):
                if "poetry" in tool:
                    facts.add("Poetry project configuration detected")
                if "uv" in tool:
                    facts.add("uv project configuration detected")

    if "Cargo.toml" in metadata:
        try:
            parsed_cargo = tomllib.loads(metadata["Cargo.toml"].decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            unknowns.append("Cargo.toml was present but could not be parsed.")
        else:
            facts.add("Rust project metadata present")
            cargo_dependencies: set[str] = set()
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                values = parsed_cargo.get(section, {})
                if isinstance(values, dict):
                    cargo_dependencies.update(values)
            for crate in sorted(cargo_dependencies & {"axum", "actix-web", "rocket", "tokio", "serde"}):
                facts.add(f"Rust dependency detected: {crate}")

    if "go.mod" in metadata:
        try:
            go_mod_text = metadata["go.mod"].decode("utf-8")
        except UnicodeDecodeError:
            unknowns.append("go.mod was present but was not valid UTF-8.")
        else:
            facts.add("Go project metadata present")
            for module, display in (
                ("github.com/gin-gonic/gin", "Gin"),
                ("github.com/gofiber/fiber", "Fiber"),
                ("github.com/labstack/echo", "Echo"),
                ("github.com/go-chi/chi", "Chi"),
            ):
                if module in go_mod_text:
                    facts.add(f"Go dependency detected: {display}")

    requirements = metadata.get("requirements.txt")
    if requirements:
        try:
            requirement_lines = requirements.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            unknowns.append("requirements.txt was present but was not valid UTF-8.")
        else:
            dependencies = [
                line.strip()
                for line in requirement_lines
                if line.strip() and not line.lstrip().startswith(("#", "-"))
            ]
            _add_dependency_facts(dependencies, PYTHON_FRAMEWORKS, facts)
            if dependencies:
                inferred.add("Python runtime inferred from requirements.txt")

    package_json = metadata.get("package.json")
    if package_json:
        try:
            parsed_package = json.loads(package_json)
        except (UnicodeDecodeError, json.JSONDecodeError):
            unknowns.append("package.json was present but could not be parsed.")
        else:
            if isinstance(parsed_package, dict):
                dependencies: list[str] = []
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    values = parsed_package.get(section, {})
                    if isinstance(values, dict):
                        dependencies.extend(str(name) for name in values)
                _add_dependency_facts(dependencies, JS_FRAMEWORKS, facts)
                if parsed_package.get("type") == "module":
                    facts.add("Node.js ES module project")

    if (root / "manage.py").is_file() and not (root / "manage.py").is_symlink():
        facts.add("Django project marker present")
    if any(name.startswith("next.config.") for name in detected_files):
        facts.add("Next.js configuration file present")
    if "angular.json" in detected_files:
        facts.add("Angular configuration file present")
    if not metadata and not any(name in detected_files for name in FRAMEWORK_CONFIGS):
        unknowns.append("No supported project metadata was detected; compatibility is task-only.")

    return ProjectSnapshot(
        path=str(root),
        detected_files=sorted(detected_files),
        facts=sorted(facts),
        inferred=sorted(inferred),
        unknowns=unknowns,
    )


def _add_dependency_facts(
    dependencies: list[object], known: dict[str, str], facts: set[str]
) -> None:
    names: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, str):
            continue
        name = dependency.split("[", 1)[0].split(" ", 1)[0].split(">", 1)[0].split("=", 1)[0]
        names.add(name.lower())
    for package, display in known.items():
        if package in names:
            facts.add(f"Dependency detected: {display}")

