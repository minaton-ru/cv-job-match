# Corporate careers sites only, no job aggregators

Vacancies are discovered only on each Company's own careers site, because the Candidate applies through those same sites. Job aggregators are deliberately not used as Sources, even though for most Companies they list more vacancies and are easier to integrate with uniformly.

The rule is about the domain, not the format. Anything served from the Company's own domain is allowed, including the JSON endpoints its careers page calls itself. That holds even when a third-party applicant tracking system sits behind it. Calling a third-party ATS or aggregator API directly is not allowed.

## Consequences

- When a Company's site blocks us, that Company is dropped. We do not fall back to an aggregator.
- Some Companies' sites refuse requests from the region the service is hosted in. Those Companies are out of scope until that changes.
