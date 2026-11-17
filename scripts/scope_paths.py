"""Keep task outputs outside the installed Skill's resource tree."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def external_output(value):
    message = 'Select an absolute output directory outside the Skill; do not use the Skill or its parent directory.'
    if value is None:
        raise ValueError(message)
    path = Path(value).expanduser()
    root = ROOT.resolve()
    # Device/extended paths may name the same Windows directory with a prefix
    # that survives resolve(). Require ordinary absolute paths on both sides.
    prefixes = ('\\\\?\\', '\\\\.\\')
    if str(path).startswith(prefixes) or str(root).startswith(prefixes):
        raise ValueError(message)
    if not path.is_absolute():
        raise ValueError(message)
    path = path.resolve()
    if str(path).startswith(prefixes) or path.is_relative_to(root) or root.is_relative_to(path):
        raise ValueError(message)
    return path
