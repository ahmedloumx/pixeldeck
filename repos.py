"""The Repos panel's routes: repositories, branches, commits and files, read from Bitbucket."""

FEATURES = []


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict or list goes out as JSON; a
    (bytes, content type) pair goes out as is."""
    return None
