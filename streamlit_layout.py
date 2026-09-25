"""Width arguments shared by modern web and legacy Oracle Streamlit runtimes."""

from importlib.metadata import PackageNotFoundError, version
import re


def layout_width(full=True):
    """Use the modern API, retaining compatibility with older local runtimes."""
    try:
        match = re.match(r"(\d+)\.(\d+)", version("streamlit"))
        modern = bool(match and tuple(map(int, match.groups())) >= (1, 51))
    except PackageNotFoundError:
        modern = True  # Pure UI tests can use a Streamlit test double.
    if modern:
        return {"width": "stretch" if full else "content"}
    return {"use_container_width": bool(full)}
