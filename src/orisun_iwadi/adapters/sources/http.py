"""HTTP session with automatic retries for flaky public APIs."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 60  # seconds


def make_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=2,  # 2s, 4s, 8s, ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "OrisunIwadi-ETL/0.1 (portfolio research project)"
    return session
