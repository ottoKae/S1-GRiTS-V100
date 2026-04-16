# setup.py
#
# PURPOSE: Force setuptools to emit a platform-specific wheel tag even though
# S1-GRiTS-V100 does not compile anything — it only packages pre-built
# .pyd (Windows) or .so (Linux) extension binaries.
#
# MECHANISM:
#   setuptools determines wheel purity by calling Distribution.has_ext_modules().
#   If that returns True, it uses the current platform's ABI tag.
#   We override it here via BinaryDistribution.
#   Without this file, `python -m build --wheel` always produces py3-none-any.
#
# CALLED BY CI AS:
#   WHEEL_PLAT_TAG=win_amd64        python -m build --wheel --no-isolation
#   WHEEL_PLAT_TAG=linux_x86_64     python -m build --wheel --no-isolation
#
# All project metadata lives in pyproject.toml. This file is intentionally minimal.

import os
from setuptools import setup
from setuptools.dist import Distribution


class BinaryDistribution(Distribution):
    """Always reports has_ext_modules=True so setuptools emits a platform wheel tag."""
    def has_ext_modules(self):
        return True


# Read the target platform tag from environment variable set by CI.
# Examples: "win_amd64", "linux_x86_64", "manylinux2014_x86_64"
_plat_tag = os.environ.get("WHEEL_PLAT_TAG", "")

_options = {}
if _plat_tag:
    # bdist_wheel reads Distribution.command_options["bdist_wheel"]["plat_name"]
    _options["bdist_wheel"] = {"plat_name": ("setup.py", _plat_tag)}
    print(f"[INFO] Forcing wheel platform tag: {_plat_tag}")

setup(
    distclass=BinaryDistribution,
    options=_options,
)
