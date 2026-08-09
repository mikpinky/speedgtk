"""Future LibreSpeed adapter boundary.

The implementation is intentionally deferred. It may use LibreSpeed's Go CLI
or a direct asynchronous HTTP client, but it must expose normalized events and
must not leak provider-specific payloads into the UI or domain layers.
"""
