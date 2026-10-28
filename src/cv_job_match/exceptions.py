"""Custom exceptions of the service."""


class SourcesConfigError(ValueError):
    """Raised when the Sources config cannot be read or is invalid."""


class HttpClientSetupError(ValueError):
    """Raised when the production HTTP client cannot be set up."""


class HttpRequestError(ConnectionError):
    """Raised when a request to a careers site gets no response."""


class SourceFetchError(RuntimeError):
    """Raised when a Source's list or a description cannot be fetched or read."""


class UnknownSourceError(LookupError):
    """Raised when no Source in the config has the requested id."""


class UnsupportedTechniqueError(LookupError):
    """Raised when no adapter handles a Source's technique yet."""


class RepositoryNotConfiguredError(RuntimeError):
    """Raised when a module needs a repository that the app was built without."""
