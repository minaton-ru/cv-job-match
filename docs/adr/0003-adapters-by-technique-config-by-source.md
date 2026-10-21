# Site adapters are named after a technique, and site details live in hidden config

Tracked code must not reveal which Companies are monitored, but every Source needs site-specific extraction. Site adapters are therefore generic and named after the technique a page uses, such as a JSON endpoint, server-rendered HTML, or state embedded in the page by its framework. They are never named after a Company. Everything that identifies a site lives in the git-ignored `sources.yaml`: URLs, request bodies, response field paths and CSS selectors. Adding a typical Source is a config change, and a new adapter is written only when a page uses a technique no existing adapter handles.

## Considered options

- One Python adapter per Source, tracked in Git. Rejected because it exposes Company names and endpoints.
- Per-Source adapters kept outside Git and loaded at runtime. Rejected because the code would be untested in CI and unreviewed.
