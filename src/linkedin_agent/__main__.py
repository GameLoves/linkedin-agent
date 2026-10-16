"""Allows `python -m linkedin_agent ...` as an alternative to the console script."""

import sys

from .cli import main

sys.exit(main())
