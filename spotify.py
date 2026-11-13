"""The music player's routes: the Spotify connection and playback, through the server."""

FEATURES = []


class SpotifyError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict or list goes out as JSON; a
    (bytes, content type) pair goes out as is."""
    return None


def callback(query):
    """The page Spotify sends the browser back to after you sign in: (status, html)."""
    return 404, "<!doctype html><title>PixelDeck</title><p>Spotify is not set up.</p>"
