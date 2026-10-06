import re
from html import escape as html_escape

from .errors import MissingVariableError

_VAR = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")


def variables_in(text):
    return set(_VAR.findall(text))


def render(text, variables, *, escape=False):
    """Replace {{name}} with variables[name]. Raises MissingVariableError listing
    every unknown name at once. With escape=True values are HTML-escaped."""
    missing = variables_in(text) - set(variables)
    if missing:
        raise MissingVariableError(missing)

    def sub(match):
        value = str(variables[match.group(1)])
        return html_escape(value) if escape else value

    return _VAR.sub(sub, text)
