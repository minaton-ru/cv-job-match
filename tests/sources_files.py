from pathlib import Path


def write_sources_yaml(directory: Path, text: str) -> Path:
    """Write a synthetic Sources config file.

    Args:
        directory: Directory to write the file into.
        text: YAML content of the file.

    Returns:
        Path to the written file.
    """
    sources_path = directory / "sources.yaml"
    sources_path.write_text(text, encoding="utf-8")
    return sources_path
