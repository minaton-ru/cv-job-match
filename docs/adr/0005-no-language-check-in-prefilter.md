# The Pre-filter does not check a Vacancy's language

Every Vacancy is assumed to be in the Candidate's configured language. Keeping other languages out is the job of each Source: its search or listing is set up in `sources.yaml` so that it lists only Vacancies in that language. The Pre-filter therefore checks only the title and city, and the Profile's `language` sets only the language of Application Packs. A Source that does list Vacancies in other languages lets them through to the AI Assessment, and the fix is to narrow that Source's config.

## Considered options

- Detecting the language of the title or description in the Pre-filter. Rejected because a short title is too little text for reliable detection, list items usually have no description yet, and it would add a dependency for a case that Source config already handles.
- Reading a language field that the Source maps. Rejected because few careers sites expose one, so the rule would rarely apply.
