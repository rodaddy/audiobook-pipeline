"""HTTP with retry, where the retry policy is tenacity's problem and not ours.

Purpose:
    Two external services -- Audible search and Audnexus -- are called once per
    book across batches of hundreds. Both rate-limit, both occasionally time
    out, and neither failure means the book is unprocessable.

WHAT IS OURS AND WHAT IS NOT
    The BACKOFF ARITHMETIC is not ours. ``_DOCS/STANDARDS-python.md`` lists the
    decisions a hand-rolled retry loop makes invisibly -- whether the delay
    before attempt N uses N or N-1, whether jitter is additive (and silently
    breaks your own max-delay bound) or subtractive -- and each is invisible in
    review, passes its tests, and surfaces during the one event it existed to
    survive. tenacity owns all of it.

    WHICH ERRORS DESERVE A RETRY is ours, because it is domain knowledge rather
    than mechanism. A 429 or a 503 is the service asking us to wait. A 404 is
    the service saying this ASIN has no chapter table, which will be just as
    true in four seconds -- retrying it three times turns a clean "no data"
    into twelve seconds of nothing per book, and across a 700-book library that
    is over two hours of waiting to learn what the first call already said.

Example:
    >>> is_retryable_status(503)
    True
    >>> is_retryable_status(404)
    False

See Also:
    - _DOCS/STANDARDS-python.md ## LAW: do not hand-roll a solved problem
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logger.bind(stage="http")

#: Status codes worth trying again. Everything else is an answer, not a
#: failure: 404 means no data exists, 400 means our request was wrong, and
#: neither improves by asking again.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Attempts, not retries -- 3 means one try plus two more. Above this the
#: service is down rather than busy, and a batch of 700 books would spend
#: longer retrying than it spends converting.
MAX_ATTEMPTS = 3

#: Seconds. Generous because Audible search is slow under load, and a timeout
#: that fires on a request that would have succeeded costs a full retry cycle.
DEFAULT_TIMEOUT = 30.0


class RetryableHttpError(httpx.HTTPError):
    """A response that failed but is worth asking about again.

    Distinct from ``httpx.HTTPStatusError`` so the retry predicate matches on
    intent rather than on status codes at the call site -- the classification
    happens once, here, and every caller inherits it.
    """


def is_retryable_status(status_code: int) -> bool:
    """Whether this status means "ask again" rather than "here is your answer".

    Args:
        status_code: HTTP status from the response.

    Returns:
        True when a retry could plausibly succeed.
    """
    return status_code in RETRYABLE_STATUS


def _raise_for_retryable(response: httpx.Response) -> None:
    """Convert a retryable status into the exception tenacity watches for.

    Args:
        response: The response to classify.

    Raises:
        RetryableHttpError: The status is transient.
        httpx.HTTPStatusError: The status is a permanent 4xx/5xx.
    """
    if is_retryable_status(response.status_code):
        msg = f"{response.status_code} from {response.request.url}"
        raise RetryableHttpError(msg)
    response.raise_for_status()


#: The retry policy, named so tests can swap the wait without redefining the
#: rest of it. A test that pays the real backoff spends four seconds proving
#: something that has nothing to do with timing -- and every retry test added
#: later pays it again, which is how a suite stops being run.
RETRY_POLICY: dict[str, Any] = {
    "stop": stop_after_attempt(MAX_ATTEMPTS),
    # Exponential with jitter, bounded. Jitter matters even single-threaded:
    # a batch resuming after a network blip would otherwise send every pending
    # book's request at the same instant and re-trigger the rate limit.
    "wait": wait_exponential_jitter(initial=1, max=30),
    "retry": retry_if_exception_type((RetryableHttpError, httpx.TransportError)),
    "reraise": True,
}


@retry(**RETRY_POLICY)
def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET a URL and return its parsed JSON, retrying transient failures.

    The client is INJECTED rather than created here, so callers share one
    connection pool across a batch and tests pass a transport that never
    touches the network.

    Args:
        client: An httpx client, owned by the caller.
        url: Absolute URL to fetch.
        params: Query parameters.

    Returns:
        The decoded JSON object.

    Raises:
        RetryableHttpError: Still failing after MAX_ATTEMPTS.
        httpx.HTTPStatusError: A permanent status such as 404.
        ValueError: The response was not a JSON object. Deliberately not
            caught: a service that started returning a list where an object
            was documented is a contract change, and treating it as empty data
            would file every book as unmatched with no explanation.
    """
    response = client.get(url, params=params)
    _raise_for_retryable(response)

    payload = response.json()
    if not isinstance(payload, dict):
        msg = f"{url} returned {type(payload).__name__}, expected a JSON object"
        raise TypeError(msg)
    return payload


def build_client(*, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Create the HTTP client used for a run.

    Args:
        timeout: Per-request timeout in seconds.

    Returns:
        A configured client. The caller owns it and is responsible for closing
        it -- normally via ``with build_client() as client:``.
    """
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "audiobook-pipeline/1.0"},
    )
