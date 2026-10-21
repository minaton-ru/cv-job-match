# No headless browser or AI site navigation for now

The original plan expected some careers sites to need browser emulation or an AI agent that navigates the site. We checked every Source in October 2026 from the region the service is hosted in. Most expose JSON endpoints on their own domain, and the rest render vacancies into static HTML, so neither a browser nor AI navigation is needed. We are not building either of them. They are the most expensive, slowest and least reliable way to extract vacancies, and nothing currently requires them.

Add Playwright if a Source appears that renders only on the client and has no usable endpoint. Treat AI navigation as the last resort after that.
