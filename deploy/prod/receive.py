#!/usr/bin/env python3
"""Production entry point with a fixed profile for the root-owned release receiver."""
from runpy import run_path


if __name__ == '__main__':
    receiver = run_path('/usr/local/sbin/semantic-bi-receive-dev')
    receiver['main'](receiver['prod_profile']())
